// =============================================================================
//  Looper OSS engine  --  glue only, no DSP written by hand.
//
//  Per track:
//    SoundIn -> gain -> gate -> highpass -> lowpass -> soft clip ------+
//        (input FX, before the write head)                             |
//                                                                      v
//    +--> layers (BufRd x maxLayers, seam fade, reverse) ----+      BufWr (record)
//    |                                                       |
//    +------------------- derived from the shared phase <----+
//                                                            |
//        -> VST slots -> delay -> fade in/out -> pan/vol ---------> mix bus
//                                                                  `-> reverb send
//  Master: mix + reverb + click -> master HP/LP -> Compander -> clip -> out
//
//  contentLen (frozen per track when it first records) vs loopLen (live grid)
//  is what makes a tempo change free varispeed: the same samples are read
//  across a different loop duration. No resampling, no buffer maths.
// =============================================================================
(
~cfg = (
	version: 2, tracks: 5, maxLayers: 4,
	bpm: 120.0, bars: 4, beatsPerBar: 4,
	audio: (blockSize: 64, hardwareBufferSize: 256, in: 2, out: 2,
		device: nil, driver: "auto", sampleRate: nil),
	loop: (maxSeconds: 16, crossfadeMs: 1.3, recordOffset: 0, autoClose: 1,
		dubDecay: 0.0, revMix: 0.6, room: 0.8, quantizeStart: 0),
	clock: (clickVol: 0.3, countInBars: 1, accentHz: 1580, clickHz: 1050),
	midi: (device: nil, map: []),
	vst: (dirs: nil),
	osc: (enginePort: 9000, statePort: 9001),
	ui: (port: 8181, wsPort: 8182, scale: 1.0, holdMs: 800, fullscreen: false, showMaster: true),
	colours: ["#e05a4f"]
);

~cfgFile = "engine/_config.scd".standardizePath;
if (File.exists(~cfgFile)) { ~cfgFile.load };

~maxTracks  = 16;
~nTracks    = ~cfg[\tracks].asInteger.clip(1, ~maxTracks);
~maxLayers  = ~cfg[\maxLayers].asInteger.clip(1, 4);
~maxSeconds = (~cfg[\loop][\maxSeconds] ? 16).asFloat.clip(4, 120);

// ------------------------------------------------------------- server setup --
~audio = ~cfg[\audio];
s = Server.default;
s.options.numInputBusChannels  = ~audio[\in].asInteger.max(2);
s.options.numOutputBusChannels = ~audio[\out].asInteger.max(2);
s.options.blockSize            = ~audio[\blockSize].asInteger.clip(16, 2048);
s.options.hardwareBufferSize   = ~audio[\hardwareBufferSize].asInteger.clip(32, 8192);
if (~audio[\sampleRate].notNil) { s.options.sampleRate = ~audio[\sampleRate] };
// "device" is an ALSA hardware name (scsynth -H). JACK/PipeWire has no device:
// scsynth is simply a node in the graph. So: jack ignores it, alsa requires it.
~driver = (~audio[\driver] ? "auto").asString.toLower;
if (~driver == "alsa") {
	s.options.device = if (~audio[\device].notNil && ~audio[\device].asString.contains("hw:"),
		{ ~audio[\device] }, { "default" });
} {
	if (~driver == "jack") {
		s.options.device = nil;
	} {
		if (~audio[\device].notNil) { s.options.device = ~audio[\device] };
	};
};
s.options.numWireBufs = 512;
s.options.memSize     = 8192 * 8;
s.options.numBuffers  = 2048;
s.options.maxLogins   = 1;
s.latency = 0.02;

// ----------------------------------------------------------------- state ----
~state = (
	bpm: ~cfg[\bpm].asFloat, bars: ~cfg[\bars].asInteger,
	beatsPerBar: ~cfg[\beatsPerBar].asInteger,
	// metronome defaults OFF: it is a click in your ears until you ask for it
	metro: 0, countIn: ~cfg[\clock][\countInBars].asInteger,
	masterVol: 0.8, clickVol: ~cfg[\clock][\clickVol].asFloat,
	sel: 0, revMix: ~cfg[\loop][\revMix].asFloat, room: ~cfg[\loop][\room].asFloat,
	xfadeMs: (~cfg[\loop][\crossfadeMs] ? 2.0).asFloat,
	recordOffset: ~cfg[\loop][\recordOffset].asInteger,
	dubDecay: ~cfg[\loop][\dubDecay].asFloat,
	autoDub: (~cfg[\loop][\autoDub] ? 1).asInteger,
	// 0 = hear only the loop: most interfaces monitor directly, and mixing our
	// delayed copy on top of that is comb filtering
	monitor: (~cfg[\loop][\monitor] ? 0).asFloat,
	autoClose: ~cfg[\loop][\autoClose].asInteger,
	quantizeStart: ~cfg[\loop][\quantizeStart].asInteger,
	masterHp: 0.0, masterLp: 18000.0,
	cpu: 0.0, version: 2,
	inL: 0.0, inR: 0.0, masterL: 0.0, masterR: 0.0, phase: 0.0
);

~loopSeconds = { ~state[\bars] * ~state[\beatsPerBar] * 60 / ~state[\bpm] };
~beatSeconds = { 60 / ~state[\bpm] };
~loopFrames  = { (~loopSeconds.value * s.sampleRate).round.asInteger.max(2) };
~phaseRate   = { 1 / (~loopSeconds.value * s.sampleRate) };
// rate is applied through here so a parked clock survives tempo changes
~applyRate   = { ~transport.set(\rate, if ((~state[\gridOn] ? 1) == 1, { ~phaseRate.value }, { 0 })) };
~trackFrames = { |ratio| (~loopFrames.value / ratio.asFloat.max(1)).round.asInteger.max(2) };
~xfadeFrames = { (~state[\xfadeMs] * 0.001 * s.sampleRate).round.asInteger.clip(1, 8192) };

~layerKeys = [\b0, \b1, \b2, \b3];
~gainKeys  = [\g0, \g1, \g2, \g3];
~vstIds    = [\fx0, \fx1, \fx2];
~hasVST    = \VSTPlugin.asClass.notNil;
if (~hasVST) {
	~vstFile = "engine/vst.scd".standardizePath;
	if (File.exists(~vstFile)) { ~vstFile.load };
};

// ------------------------------------------------------------------ defs ----
~addDefs = {

	// ---- master clock: the shared grid -------------------------------------
	SynthDef(\ld_transport, { |phaseBus, clickBus, rate = 0.001,
			beatsPerBar = 4, bars = 4, metro = 1, countIn = 0,
			clickAmp = 0.3, accentHz = 1580, clickHz = 1050, reportRate = 15|
		var ph, phKr, beats, beatTr, isBarTop, freq, click, wrap, playClick;
		ph    = Phasor.ar(0, rate, 0, 1, 0);
		phKr  = A2K.kr(ph);
		beats = phKr * (beatsPerBar * bars).max(1);
		beatTr   = Changed.kr(beats.floor);
		isBarTop = Select.kr((beats.floor % beatsPerBar) < 0.5, [0, 1]);
		freq     = Select.kr(isBarTop, [clickHz, accentHz]);
		click    = SinOsc.ar(freq) * EnvGen.ar(Env.perc(0.0005, 0.05), beatTr);
		wrap     = Changed.kr(phKr < 0.5) * (phKr < 0.5);
		playClick = (metro + countIn).clip(0, 1) * clickAmp;
		Out.ar(phaseBus, ph);
		Out.ar(clickBus, (click * playClick) ! 2);

		SendReply.kr(beatTr, '/ldbeat', [(beats.floor % beatsPerBar)]);
		SendReply.kr(wrap, '/ldwrap');
	}).add;

	// ---- one track ----------------------------------------------------------
	~trackDef = { |postFx = nil|
		// BufRd's `loop` flag wraps at the buffer size, not at our loop length, so a
	// fractional position just under contentLen used to interpolate against zero.
	// This reads floor/ceil explicitly with a modulo on the loop length, which makes
	// the seam interpolate between the last and the first sample -- continuous.
	~lerpRead = { |buf, pos, loopLen|
		var i0 = pos.floor;
		var f  = pos - i0;
		var a  = BufRd.ar(1, buf, i0 % loopLen, 0, 1);
		var c  = BufRd.ar(1, buf, (i0 + 1) % loopLen, 0, 1);
		a + ((c - a) * f)
	};

	SynthDef(\ld_track, { |outBus, revBus, phaseBus, idx = 0,
				b0 = 0, b1 = 0, b2 = 0, b3 = 0,
				g0 = 0, g1 = 0, g2 = 0, g3 = 0,
				testBus = 0, scratch = 0, recOn = 0, playing = 0,
				w0 = 0, w1 = 0, w2 = 0, w3 = 0,
				loopLen = 48000, contentLen = 48000, loopRatio = 1,
				xfade = 64, recordOffset = 0, reverse = 0,
				vol = 0.8, pan = 0, muted = 0, monitor = 0.8,
				input = 0, gain = 1.0, recGain = 1.0,
				gateOn = 1, gateThr = 0.02, autoArm = 0, autoThresh = 0.02,
				hp = 40, lp = 16000,
				delayOn = 0, delayBeats = 0.75, delayDecay = 0.4, delayMix = 0.25,
				revSend = 0.0, beatLen = 0.5, loopSec = 8,
				fadeIn = 0, fadeOut = 0, vB0 = 0, vB1 = 0, vB2 = 0|

			var tph, pos, wpos, raw, gateEnv, gate, sig, sigR, layers, src,
			    dl, wet, dst, fades, st, seam, xfd, rpos, playGain;

			// track phase: ratio 2 => this track loops twice per master loop
			tph  = (In.ar(phaseBus, 1) * loopRatio) % 1;
			// pos spans the WHOLE loop, [0, contentLen). Using (contentLen - 1) here
			// left the final frame unwritten -- it kept the buffer's zero, so every
			// take ended in a full-amplitude step at the loop point.
			pos  = tph * contentLen.max(2);
			xfd  = xfade.clip(1, (contentLen / 2).max(1));
			// seam fade: zero at both ends of the loop, so a wrap cannot click
			seam = ((pos + 1) / xfd).min((contentLen - pos) / xfd).clip(0, 1);
			// raised cosine: a linear ramp has a kink at each end and that kink is
			// broadband energy, audible as a buzz at the loop point
			seam = 0.5 - (0.5 * (pi * seam).cos);
			rpos = Select.ar(K2A.ar(reverse), [pos, (contentLen - 1) - pos]);
			rpos = rpos.clip(0, (contentLen - 1).max(1));
			wpos = ((rpos - recordOffset) % contentLen).clip(0, (contentLen - 1).max(1));
			// BufWr truncates its index and float lands at 383998.9999 for the final
			// sample, so the last frame was never written and kept the buffer's zero.
			// Rounding the write index covers 0 .. contentLen-1 exactly.
			wpos = (wpos + 0.5).floor;

			// ---- input FX (before the write head) ----
			// input 3 is an internal test bus: it lets the record path be verified
			// with a known signal instead of a microphone. The UI never offers it.
			raw     = Select.ar(K2A.ar(input), [
				(SoundIn.ar(0) + SoundIn.ar(1)) * 0.7071,
				SoundIn.ar(0),
				SoundIn.ar(1),
				In.ar(testBus, 1)
			]) * gain;
			gateEnv = Amplitude.kr(raw, 0.002, 0.05);
			gate    = Select.kr(gateOn, [1, LagUD.kr((gateEnv > gateThr), 0.003, 0.06)]);
			sig     = BHiPass.ar(raw, hp.clip(10, 20000), 0.707);
			sig     = BLowPass.ar(sig, lp.clip(200, 19000), 0.707) * gate;
			sig     = (sig * recGain).tanh;

			// ---- layers ----
			layers =
				(~lerpRead.value(b0, rpos, contentLen) * LagUD.kr(g0, 0.01, 0.01)) +
				(~lerpRead.value(b1, rpos, contentLen) * LagUD.kr(g1, 0.01, 0.01)) +
				(~lerpRead.value(b2, rpos, contentLen) * LagUD.kr(g2, 0.01, 0.01)) +
				(~lerpRead.value(b3, rpos, contentLen) * LagUD.kr(g3, 0.01, 0.01));

			// One writer per layer, each bound to its OWN buffer when the synth is
			// built -- no runtime buffer switching. w<i> selects "record the input"
			// vs "write my own content back", and writing back is a no-op.
			// sigR carries the 3 ms record ramp and IS written into the buffer: that
			// ramp is the punch-out fade. ~commit drops recOn first and closes the
			// write gate 30 ms later, so a take never ends in a step to zero. Steady
			// state is recOn == 1, so the ramp does not touch the body of a take.
			sigR = sig * LagUD.kr(recOn, 0.003, 0.003);
			BufWr.ar(Select.ar(K2A.ar(w0), [BufRd.ar(1, b0, wpos, 1, 1), sigR]), b0, wpos);
			BufWr.ar(Select.ar(K2A.ar(w1), [BufRd.ar(1, b1, wpos, 1, 1), sigR]), b1, wpos);
			BufWr.ar(Select.ar(K2A.ar(w2), [BufRd.ar(1, b2, wpos, 1, 1), sigR]), b2, wpos);
			BufWr.ar(Select.ar(K2A.ar(w3), [BufRd.ar(1, b3, wpos, 1, 1), sigR]), b3, wpos);


			src = (layers * seam) + (sig * monitor * LagUD.kr(recOn, 0.01, 0.01));

			// ---- plugin inserts (engine/vst.scd draws them) ----
			src = if (postFx.notNil, { postFx.value(src, [vB0, vB1, vB2]) }, { src });

			dl  = (delayBeats * beatLen).clip(0.01, 1.9);
			wet = CombN.ar(src, 2.0, dl, delayDecay.clip(0.02, 8));
			dst = Select.ar(K2A.ar(delayOn), [src, (src * (1 - delayMix)) + (wet * delayMix)]);

			fades = (tph * loopSec / fadeIn.max(0.001))
				.min(((1 - tph) * loopSec / fadeOut.max(0.001)))
				.min(1).clip(0, 1);
			dst = dst * Select.ar(K2A.ar((fadeIn + fadeOut) > 0), [DC.ar(1.0), fades]);

			playGain = LagUD.kr(playing, 0.005, 0.005) * (1 - LagUD.kr(muted, 0.01, 0.01));
			// mono source -> both channels explicitly, then equal-power pan.
			// Pan2 alone left a mono source only on the left channel in practice.
			dst = dst * playGain * LagUD.kr(vol, 0.01, 0.01);
			st = [
				dst * ((1 - LagUD.kr(pan, 0.01, 0.01)) * 0.5).sqrt,
				dst * ((1 + LagUD.kr(pan, 0.01, 0.01)) * 0.5).sqrt
			];

			Out.ar(outBus, st);
			Out.ar(revBus, st.sum * LagUD.kr(revSend, 0.02, 0.02));
		}).add;
	};
	~trackDef.value(~vstPost);

	// ---- meters -------------------------------------------------------------
	// These are separate tiny defs on purpose: the same SendReply inside the big
	// track def produced 1- and 2-value messages at ~120 Hz per track instead of
	// one 4-value message at 15 Hz. Small defs behave exactly.
	SynthDef(\ld_meter, { |bus = 0, idx = 0, meterBuf = 0|
		var sig = In.ar(bus, 2);
		var base = idx * 4;
		BufWr.kr(Amplitude.kr(sig[0], 0.001, 0.08), meterBuf, base);
		BufWr.kr(Amplitude.kr(sig[1], 0.001, 0.08), meterBuf, base + 1);
		BufWr.kr(Amplitude.kr(sig.sum, 0.001, 0.25), meterBuf, base + 2);
	}).add;

	// threshold recorder trigger (its own tiny def, like the meters)
	SynthDef(\ld_autodetect, { |idx = 0, arm = 0, thresh = 0.02, input = 0, gain = 1|
		var raw = Select.ar(K2A.ar(input), [
			(SoundIn.ar(0) + SoundIn.ar(1)) * 0.7071,
			SoundIn.ar(0),
			SoundIn.ar(1)
		]) * gain;
		var env = Amplitude.kr(raw, 0.002, 0.05);
		var trig = Trig1.kr((env > thresh) * LagUD.kr(arm, 0.001, 0.001), 0.02);
		SendReply.kr(trig, '/ldauto', [idx]);
	}).add;

	// master out + hardware in, in the same buffer (slots 60..63)
	SynthDef(\ld_global_meter, { |meterBuf = 0, slot = 60|
		BufWr.kr(Amplitude.kr(In.ar(0, 1), 0.001, 0.08), meterBuf, slot);
		BufWr.kr(Amplitude.kr(In.ar(1, 1), 0.001, 0.08), meterBuf, slot + 1);
		BufWr.kr(Amplitude.kr(SoundIn.ar(0), 0.001, 0.08), meterBuf, slot + 2);
		BufWr.kr(Amplitude.kr(SoundIn.ar(1), 0.001, 0.08), meterBuf, slot + 3);
	}).add;

	// ---- master: mix + reverb + click, master FX, limiter ------------------
	SynthDef(\ld_master, { |mixBase, revBus, clickBus, masterBus, vol = 0.8,
			revMix = 0.6, room = 0.8, damp = 0.3, clickVol = 0.3,
			masterHp = 0, masterLp = 18000|
		var rv, dry, wet, click, full, sig, mfx;
		rv    = In.ar(revBus, 2);
		dry   = In.ar(mixBase, 32).clump(2).sum;
		wet   = FreeVerb2.ar(rv[0], rv[1], 1, room, damp);
		click = In.ar(clickBus, 2) * clickVol;
		full  = dry + (wet * revMix) + click;
		mfx   = ((masterHp > 1) + (masterLp < 19999)).clip(0, 1);
		full  = Select.ar(K2A.ar(mfx), [
			full,
			BLowPass.ar(BHiPass.ar(full, masterHp.clip(20, 2000), 0.707),
				masterLp.clip(1000, 19000), 0.707)
		]);
		sig = Compander.ar(full, full, 0.9, 1, 0.1, 0.001, 0.1).clip2(0.98);
		Out.ar(masterBus, sig * vol);
		Out.ar(0, sig * vol);
	}).add;

	// ---- session capture ---------------------------------------------------
	SynthDef(\ld_capture, { |masterBus, buf, doneId = -1|
		var sig = In.ar(masterBus, 2);
		RecordBuf.ar(sig, buf, loop: 0, doneAction: 2);
		SendReply.kr(Done.kr(Line.kr(0, 1, BufDur.kr(buf))), '/ldcaptured', [doneId]);
	}).add;
};

// ---------------------------------------------------------------- helpers ----
~anyLoops = { ~tracks.any { |t| t[\layerCount] > 0 } };

~contentLenNow = { |t| ~trackFrames.value(t[\loopRatio]) };

~pushTrack = { |t, key, v| t[key.asSymbol] = v; t[\synth].set(key.asSymbol, v) };

~pushAll = { |t, keys|
	keys.do { |k| t[\synth].set(k.asSymbol, t[k.asSymbol]) };
};

// ------------------------------------------------------------------ boot ----
~boot = {
	~addDefs.value;
	s.sync;

	~phaseBus  = Bus.audio(s, 1);
	~mixBus    = Bus.audio(s, ~maxTracks * 2);
	~revBus    = Bus.audio(s, 2);
	~clickBus  = Bus.audio(s, 2);
	~testBus   = Bus.audio(s, 1);      // diagnostics only
	~masterBus = Bus.audio(s, 2);
	~maxFrames = (~maxSeconds * s.sampleRate).round.asInteger;
	~scratch   = Buffer.alloc(s, ~maxFrames, 1);
	~meterBuf  = Buffer.alloc(s, 64, 1);
	// explicit order: clock -> tracks -> master. Synth() defaults to \addToHead,
	// so a group target alone would run the master BEFORE the tracks it sums.
	~grpClock  = Group.new;
	~grpTracks = Group.after(~grpClock);
	~grpMaster = Group.after(~grpTracks);

	~transport = Synth(\ld_transport, [
		\phaseBus, ~phaseBus, \clickBus, ~clickBus,
		\rate, ~phaseRate.value,
		\beatsPerBar, ~state[\beatsPerBar], \bars, ~state[\bars],
		\metro, ~state[\metro], \countIn, 0, \clickAmp, 1,
		\accentHz, ~cfg[\clock][\accentHz], \clickHz, ~cfg[\clock][\clickHz]
	], ~grpClock, \addToTail);

	~master = Synth(\ld_master, [
		\mixBase, ~mixBus, \revBus, ~revBus, \clickBus, ~clickBus,
		\masterBus, ~masterBus, \vol, ~state[\masterVol],
		\revMix, ~state[\revMix], \room, ~state[\room],
		\clickVol, ~state[\clickVol],
		\masterHp, ~state[\masterHp], \masterLp, ~state[\masterLp]
	], ~grpMaster, \addToTail);
	Synth(\ld_global_meter, [\meterBuf, ~meterBuf, \slot, 60], ~grpMaster, \addToTail);
	s.sync;

	~tracks = Array.fill(~nTracks, { |i|
		(
			idx: i,
			bufs: [], gains: Array.fill(~maxLayers, 0),
			layerCount: 0, undone: 0, passes: 0,
			state: 0, playing: 0, recOn: 0,
			vol: 0.8, pan: 0.0, muted: 0, input: 0, gain: 1.0, recGain: 1.0,
			gateOn: 1, gateThr: 0.02, autoArm: 0, autoThresh: 0.02,
			hp: 40, lp: 16000,
			delayOn: 0, delayBeats: 0.75, delayDecay: 0.4, delayMix: 0.25,
			revSend: 0.0, reverse: 0, fadeIn: 0, fadeOut: 0,
			loopRatio: 1, contentLen: 0,
			meterL: 0, meterR: 0, layerPeak: 0, meterPeak: 0, meterSum: 0, cpu: 0,
			vst: [nil, nil, nil], vstNames: ["", "", ""], vstBypass: [0, 0, 0],
			vstGui: [nil, nil, nil],
			synth: nil
		)
	});

	~makeBuffers = {
		~tracks.do { |t|
			t[\bufs].do { |b| b.free };
			t[\bufs] = Array.fill(~maxLayers, { Buffer.alloc(s, ~maxFrames, 1) });
		};
		s.sync;
	};

	~makeBuffers.value;

	~tracks.do { |t, i|
		var args = [
			\outBus, ~mixBus.index + (i * 2), \revBus, ~revBus,
			\phaseBus, ~phaseBus, \idx, i, \testBus, ~testBus,
			\scratch, ~scratch.bufnum,
			\loopLen, ~loopFrames.value, \loopRatio, t[\loopRatio],
			\contentLen, ~contentLenNow.value(t),
			\xfade, ~xfadeFrames.value, \recordOffset, ~state[\recordOffset],
			\beatLen, ~beatSeconds.value, \loopSec, ~loopSeconds.value,
			\vol, t[\vol], \pan, t[\pan], \gateOn, t[\gateOn],
			\gateThr, t[\gateThr], \hp, t[\hp], \lp, t[\lp],
			\input, t[\input], \delayDecay, t[\delayDecay], \playing, 0,
			\monitor, ~state[\monitor]
		];
		4.do { |li|
			var b = if (li < ~maxLayers, { t[\bufs][li].bufnum }, { ~scratch.bufnum });
			args = args ++ [~layerKeys[li], b, ~gainKeys[li], 0,
				("w" ++ li).asSymbol, 0];
		};
		t[\synth] = Synth(\ld_track, args, ~grpTracks, \addToTail);
		t[\meterSynth] = Synth(\ld_meter,
			[\bus, ~mixBus.index + (i * 2), \idx, i, \meterBuf, ~meterBuf],
			~grpTracks, \addToTail);
		t[\autoSynth] = Synth(\ld_autodetect,
			[\idx, i, \arm, 0, \thresh, t[\autoThresh], \input, t[\input],
			 \gain, t[\gain]], ~grpTracks, \addToTail);
	};
	s.sync;

	~initVST.value;
	~initMIDI.value;

	"--- Looper OSS ------------------------------------------".postln;
	"tracks % | layers % | loop % s | max % s | VST %".format(
		~nTracks, ~maxLayers, ~loopSeconds.value.round(0.01), ~maxSeconds,
		if (~hasVST, "yes", "no")).postln;
};

// ------------------------------------------------------------- actions ------
(
	~startRec = { |t|
		~state[\gridOn] = 1; ~applyRate.value;     // a take needs the clock
		if (t[\layerCount] < ~maxLayers) {
			var li  = t[\layerCount];
			var buf = t[\bufs][li];
			if (t[\layerCount] == 0) {
				// first layer freezes this track's sample length => varispeed later
				t[\contentLen] = ~contentLenNow.value(t);
				t[\synth].set(\contentLen, t[\contentLen]);
			};
			buf.zero;
			t[\synth].set(("w" ++ li).asSymbol, 1, \recOn, 1, \playing, 1);
			t[\recOn] = 1; t[\playing] = 1; t[\state] = 1;
			t[\passes] = 0;
		};
	};

	~commit = { |t, atBoundary = false|
		var li = t[\layerCount];
		if (t[\recOn] == 1) {
			if (atBoundary) {
				// the loop joins itself here: no fade, the seam window handles it
				t[\synth].set(("w" ++ li).asSymbol, 0, \recOn, 0);
			} {
				// punch-out mid-pass: fade the input into the buffer first, or the
				// take ends in a step from full level to zero (an audible click)
				t[\synth].set(\recOn, 0);
				{ t[\synth].set(("w" ++ li).asSymbol, 0) }.defer(0.03);
			};
			t[\recOn] = 0;
			t[\layerCount] = li + 1;
			t[\undone] = 0;
			t[\gains][li] = 1;
			t[\synth].set(~gainKeys[li], 1);
			t[\state] = 2;
		};
	};

	~stopTrack = { |t|
		~commit.value(t);          // also clears recOn, so the dry input goes away
		t[\synth].set(\playing, 0);
		t[\playing] = 0;
		t[\state] = if (t[\layerCount] > 0, 3, 0);
	};

	// The track button plays and stops. It does NOT start an overdub: on the
	// hardware that is a separate DUB action, and pressing a playing track to make
	// a transition must stop it, not silently start recording.
	~press = { |t|
		if (t[\recOn] == 1) {
			~commit.value(t);
		} {
			if (t[\playing] == 1) {
				~stopTrack.value(t);
			} {
				if (t[\layerCount] > 0) {
					t[\playing] = 1; t[\synth].set(\playing, 1); t[\state] = 2;
				} {
					~startRec.value(t);
				};
			};
		};
	};

	// explicit overdub
	~dub = { |t|
		if (t[\recOn] == 1) { ~commit.value(t) } { ~startRec.value(t) };
	};

	~clearTrack = { |t|
		~maxLayers.do { |li| t[\synth].set(("w" ++ li).asSymbol, 0) };
		t[\synth].set(\recOn, 0, \playing, 0);
		~maxLayers.do { |li| t[\synth].set(~gainKeys[li], 0) };
		t[\gains] = Array.fill(~maxLayers, 0);
		t[\layerCount] = 0; t[\undone] = 0; t[\passes] = 0;
		t[\recOn] = 0; t[\playing] = 0; t[\state] = 0;
		t[\contentLen] = ~contentLenNow.value(t);
		t[\synth].set(\contentLen, t[\contentLen]);
		// the user erased the last material: drop the autosave with it, so a
		// stopped session cannot come back
		if (~anyLoops.value.not && ((~loading ? false) != true)) {
			~clearAutosave.value;
		};
	};

	~undo = { |t|
		~commit.value(t);
		if (t[\layerCount] > 0) {
			var li = t[\layerCount] - 1;
			t[\layerCount] = li;
			t[\gains][li] = 0;
			t[\synth].set(~gainKeys[li], 0);
			t[\undone] = t[\undone] + 1;
			t[\state] = if (li > 0, 2, 0);
		};
	};

	~redo = { |t|
		if ((t[\layerCount] < ~maxLayers) && (t[\undone] > 0)) {
			var li = t[\layerCount];
			t[\gains][li] = 1;
			t[\synth].set(~gainKeys[li], 1);
			t[\layerCount] = li + 1;
			t[\undone] = t[\undone] - 1;
			t[\state] = 2;
		};
	};

	// dub mode: every layer loses dubDecay of its gain per loop pass
	~dubPass = {
		if (~state[\dubDecay] > 0.001) {
			var f = 1 - ~state[\dubDecay].clip(0, 0.95);
			~tracks.do { |t|
				~maxLayers.do { |li|
					if (t[\gains][li] > 0.0005) {
						t[\gains][li] = t[\gains][li] * f;
						t[\synth].set(~gainKeys[li], t[\gains][li]);
					};
				};
			};
		};
	};

	~setGlobal = { |key, v|
		switch (key,
			"bpm", {
				if (v > 0) {
					~state[\bpm] = v;
					~applyRate.value;
					~tracks.do { |t|
						t[\synth].set(\beatLen, ~beatSeconds.value, \loopSec, ~loopSeconds.value);
						if (t[\layerCount] == 0) {
							t[\contentLen] = ~contentLenNow.value(t);
							t[\synth].set(\contentLen, t[\contentLen]);
						};
					};
				};
			},
			"bars", {
				~state[\bars] = v.asInteger.max(1);
				~transport.set(\bars, ~state[\bars]);
				~applyRate.value;
				~tracks.do { |t|
					t[\synth].set(\loopSec, ~loopSeconds.value);
					if (t[\layerCount] == 0) {
						t[\contentLen] = ~contentLenNow.value(t);
						t[\synth].set(\contentLen, t[\contentLen]);
					};
				};
			},
			"beatsPerBar", {
				~state[\beatsPerBar] = v.asInteger.clip(1, 16);
				~transport.set(\beatsPerBar, ~state[\beatsPerBar]);
				~applyRate.value;
				~tracks.do { |t| t[\synth].set(\loopSec, ~loopSeconds.value) };
			},
			"metro",       { ~state[\metro] = v.asInteger; ~transport.set(\metro, ~state[\metro]) },
			"countIn",     { ~state[\countIn] = v.asInteger.clip(0, 8) },
			"masterVol",   { ~state[\masterVol] = v; ~master.set(\vol, v) },
			"clickVol",    { ~state[\clickVol] = v; ~master.set(\clickVol, v) },
			"revMix",      { ~state[\revMix] = v; ~master.set(\revMix, v) },
			"room",        { ~state[\room] = v; ~master.set(\room, v); ~master.set(\damp, 1 - v) },
			"masterHp",    { ~state[\masterHp] = v; ~master.set(\masterHp, v) },
			"masterLp",    { ~state[\masterLp] = v; ~master.set(\masterLp, v) },
			"xfadeMs",     {
				~state[\xfadeMs] = v.clip(0.1, 50);
				~tracks.do { |t| t[\synth].set(\xfade, ~xfadeFrames.value) };
			},
			"recordOffset", {
				~state[\recordOffset] = v.asInteger.clip(-8192, 8192);
				~tracks.do { |t| t[\synth].set(\recordOffset, ~state[\recordOffset]) };
			},
			"dubDecay",    { ~state[\dubDecay] = v.clip(0, 0.95) },
			"monitor",     {
				// dry input heard while recording: 0 = hear only the loop
				~state[\monitor] = v.clip(0, 1.5);
				~tracks.do { |t| t[\synth].set(\monitor, ~state[\monitor]) };
			},
			"autoClose",   { ~state[\autoClose] = v.asInteger },
			"autoDub",     { ~state[\autoDub] = v.asInteger },
			"autosave",    {
				~state[\autosave] = v.asInteger;
				if (v.asInteger == 0) { ~clearAutosave.value };
			},
			"quantizeStart", { ~state[\quantizeStart] = v.asInteger },
			"sel",         { ~state[\sel] = v.asInteger }
		);
	};

	~setTrack = { |i, key, v|
		var t = ~tracks[i];
		if (t.notNil) {
			if (key == "loopRatio") {
				t[\loopRatio] = v.asInteger.clip(1, 8);
				t[\synth].set(\loopRatio, t[\loopRatio]);
				if (t[\layerCount] == 0) {
					t[\contentLen] = ~contentLenNow.value(t);
					t[\synth].set(\contentLen, t[\contentLen]);
				};
			} {
				if (key == "delayBeats") {
					t[\delayBeats] = v;
					t[\synth].set(\delayBeats, v, \beatLen, ~beatSeconds.value);
				} {
					~pushTrack.value(t, key, v);
				};
			};
		};
	};

	~applySet = { |path, v|
		var parts = path.asString.split($.);
		var head  = parts[0];
		if (head == "g") {
			~setGlobal.value(parts[1..].join($.), v);
		} {
			~setTrack.value(head[1..].asInteger, parts[1..].join($.), v);
		};
	};

	~act = { |action, i|
		var t = ~tracks[i.asInteger];
		switch (action.asString,
			"press",  { if (t.notNil) { ~press.value(t) } },
			"dub",    { if (t.notNil) { ~dub.value(t) } },
			"stop",   { if (t.notNil) { ~stopTrack.value(t) } },
			"clear",  { if (t.notNil) { ~clearTrack.value(t) } },
			"undo",   { if (t.notNil) { ~undo.value(t) } },
			"redo",   { if (t.notNil) { ~redo.value(t) } },
			"stopAll",{
				~tracks.do { |tr| ~stopTrack.value(tr) };
				~state[\gridOn] = 0; ~applyRate.value;    // playhead + click stop
			},
			"shutdown", {
				// asked by the bridge when the UI has gone away
				"Looper OSS: shutting down".postln;
				s.quit;
				0.exit;
			},
			"reboot", {
				// the device must be free before the next scsynth starts, so quit,
				// let the process die, and only then ask run.sh to restart us
				"reboot requested".postln;
				fork { s.quit; 2.5.wait; 42.exit };
			},
			"save",   { ~saveSession.value(i.asInteger) },
			"load",   { ~loadSession.value(i.asInteger) },
			"export", { ~exportMix.value(30) }
		);
	};
);

// ---------------------------------------------------------------- session ---
(
	~sessionRoot = "sessions".standardizePath;
	~exportCount = 0;

	// one writer for slots and the autosave: dir + a tag (negative = autosave)
	~writeSession = { |dir, tag|
		var written = 0;
		File.mkdir(~sessionRoot);
		File.mkdir(dir);
		fork {
			~tracks.do { |t, i|
				var n = t[\layerCount];
				~maxLayers.do { |li|
					var f = dir +/+ ("t%_l%.wav".format(i, li));
					if (li < n) {
						t[\bufs][li].write(f, "WAV", "float", t[\contentLen]);
						written = written + 1;
					} {
						if (File.exists(f)) { File.delete(f) };
					};
				};
			};
			s.sync;
			~vstSaveState.value(dir);
			~bridge.sendMsg('/ldsaved', tag, written, dir);
		};
	};

	~saveSession = { |slot|
		~writeSession.value(~sessionRoot +/+ ("slot" ++ slot), slot.asInteger);
	};

	~autosaveDir = { ~sessionRoot +/+ "autosave" };
	~autosaveSig = nil;

	// A cleared track must clear its autosave too, or a stopped session brings
	// back the layers you just erased on the next start.
	~clearAutosave = {
		var dir = ~autosaveDir.value;
		~tracks.do { |t, i|
			~maxLayers.do { |li|
				var f = dir +/+ ("t%_l%.wav".format(i, li));
				if (File.exists(f)) { File.delete(f) };
			};
		};
		if (File.exists(dir +/+ "session.json")) { File.delete(dir +/+ "session.json") };
		if (File.exists(dir +/+ "vst.txt")) { File.delete(dir +/+ "vst.txt") };
		~autosaveSig = nil;
	};

	// called once a second: save when the layer structure changed and nothing is
	// being recorded, so a crash costs at most one layer
	~autosaveTick = {
		var sig = ~tracks.collect { |t| t[\layerCount] }.join("-");
		// No "is anything recording?" guard: with autoDub a track can be recording
		// continuously, which made autosave never fire. Only *committed* layers are
		// written anyway, so a take in progress is simply not part of the snapshot.
		if (((~state[\autosave] ? 1) == 1) && (sig != ~autosaveSig)) {
			~autosaveSig = sig;
			// save only. Deleting here once wiped an autosave that was still being
			// recovered; clearing is now a consequence of an explicit clear.
			if (~tracks.any { |t| t[\layerCount] > 0 }) {
				~writeSession.value(~autosaveDir.value, -1);
			};
		};
	};

	~loadSessionDir = { |dir, tag|
				if (File.exists(dir).not) {
			~bridge.sendMsg('/ldloaderr', tag, "no session");
		} {
			fork {
				var loaded = 0;
				~loading = true;                 // keep ~clearTrack off the autosave
				~tracks.do { |t, i|
					var frames, n = 0;
					~clearTrack.value(t);
					~maxLayers.do { |li|
						var f = dir +/+ ("t%_l%.wav".format(i, li));
						var tmp;
						if (File.exists(f)) {
							tmp = Buffer.read(s, f);
							s.sync;
							if (tmp.numFrames.isNil || (tmp.numFrames < 1)) {
								// an interrupted save: skip it rather than fail the load
								tmp.free;
								tmp = nil;
							};
						};
						if (tmp.notNil) {
							if (frames.isNil) {
								frames = tmp.numFrames.min(~maxFrames);
							};
							// Wipe the whole layer first. Playback interpolates a few
							// samples past the loop end, and a shorter loop loaded into a
							// buffer that held a longer one leaves real audio there --
							// audible as garbage on the loop point after a restart.
							t[\bufs][li].zero;
							// copyData(dest, dstStart, srcStart, numSamples) -- the
							// arg order is easy to get wrong and silently copies 0
							tmp.copyData(t[\bufs][li], 0, 0, frames.min(tmp.numFrames));
							tmp.free;
							n = li + 1;
							loaded = loaded + 1;
						};
					};
					s.sync;
					if (n > 0) {
						t[\contentLen] = frames;
						t[\layerCount] = n;
						t[\gains] = Array.fill(~maxLayers, { |li| if (li < n, 1, 0) });
						~maxLayers.do { |li| t[\synth].set(~gainKeys[li], t[\gains][li]) };
						// An autosave recovery (tag < 0) restores the layers but does NOT
						// start playing: opening the app must never blast the last session
						// at you. An explicit slot load (tag >= 0) does start playing.
						t[\synth].set(\contentLen, frames, \recOn, 0,
							\playing, if (tag >= 0, 1, 0));
						t[\playing] = if (tag >= 0, 1, 0);
						t[\state] = if (tag >= 0, 2, 3);
					};
				};
				~vstLoadState.value(dir);
				~loading = false;
				~autosaveSig = ~tracks.collect { |t| t[\layerCount] }.join("-");
				~bridge.sendMsg('/ldloaded', tag, loaded, dir);
			};
		};
	};

	~loadSession = { |slot|
		~loadSessionDir.value(~sessionRoot +/+ ("slot" ++ slot), slot.asInteger);
	};
	~exportMix = { |seconds = 30|
		var frames = (seconds * s.sampleRate).round.asInteger;
		File.mkdir(~sessionRoot);
		~captureBuf = Buffer.alloc(s, frames, 2);
		Synth(\ld_capture, [\masterBus, ~masterBus, \buf, ~captureBuf, \doneId, 1],
			~grpClock);
		~bridge.sendMsg('/ldcapturing', seconds);
	};

	OSCdef(\ldCaptured, { |msg|
		var buf = ~captureBuf;
		if (buf.notNil) {
			var dir = ~sessionRoot +/+ "exports";
			var f;
			~exportCount = ~exportCount + 1;
			File.mkdir(dir);
			f = dir +/+ ("mixdown_%_%.wav".format(Date.getDate.stamp, ~exportCount));
			fork {
				buf.write(f, "WAV", "float");
				s.sync;
				buf.free;
				~captureBuf = nil;
				~bridge.sendMsg('/ldexported', f);
			};
		};
	}, '/ldcaptured');
);

// -------------------------------------------------------------------- vst ---
// Real implementation in engine/vst.scd, loaded only when the extension exists.
// These stubs must NOT overwrite it, hence the guard.
(
	if (~hasVST.not) {
		~vstPost = nil;
		~vstCount = { 0 };
		~initVST = { nil };
		~sendVSTList = { nil };
		~sendVSTCatalog = { nil };
		~vstOpen = { |trackIdx, slot, name| nil };
		~vstClose = { |trackIdx, slot| nil };
		~vstBypass = { |trackIdx, slot, on| nil };
		~vstEditor = { |trackIdx, slot| nil };
		~vstSaveState = { |dir| nil };
		~vstLoadState = { |dir| nil };
	};
);

// ------------------------------------------------------------------- midi ---
(
	~midiMap = [];
	~learnTarget = nil;
	~midiDevice = -1;          // -1 = listen to every source

	// MIDIIn has no `sources` in 3.14; MIDIClient holds the endpoint lists
	~midiSourcesCSV = {
		var out = "";
		protect {
			var srcs = MIDIClient.sources;
			if (srcs.notNil) {
				out = srcs.collect { |ep, i| "%|%".format(i, ep.name) }.join(";").asString;
			};
		};
		out
	};

	~sendMIDIDevices = {
		~bridge.sendMsg('/ldmididevs', ~midiSourcesCSV.value);
	};

	// standard tabletop/footswitch layout: 4 CCs per track + 3 global
	~midiDefault = {
		var m = List.new;
		8.do { |i|
			m.add((type: "cc", num: 20 + i, target: "t%.press".format(i)));
			m.add((type: "cc", num: 30 + i, target: "t%.undo".format(i)));
			m.add((type: "cc", num: 40 + i, target: "t%.stop".format(i)));
			m.add((type: "cc", num: 50 + i, target: "t%.clear".format(i)));
		};
		m.add((type: "cc", num: 60, target: "g.tap"));
		m.add((type: "cc", num: 61, target: "g.stopAll"));
		m.add((type: "cc", num: 62, target: "g.metro"));
		~midiMap = m.asArray;
		~sendMIDIMap.value;
	};

	~midiAccepts = { |src| (~midiDevice ? -1) < 0 or: { src == ~midiDevice } };

	~fireMIDI = { |target, val|
		var parts = target.asString.split($.);
		var head = parts[0];
		var verb = parts[1];
		if (head == "g") {
			switch (verb,
				"tap",     { ~tapTempo.value },
				"stopAll", { ~tracks.do { |t| ~stopTrack.value(t) } },
				"metro",   { ~setGlobal.value("metro", 1 - ~state[\metro]) }
			);
		} {
			var t = ~tracks[head[1..].asInteger];
			if (t.notNil) {
				switch (verb,
					"press", { if (val > 0) { ~press.value(t) } },
					"stop",  { if (val > 0) { ~stopTrack.value(t) } },
					"clear", { if (val > 0) { ~clearTrack.value(t) } },
					"undo",  { if (val > 0) { ~undo.value(t) } },
					"redo",  { if (val > 0) { ~redo.value(t) } }
				);
			};
		};
	};

	~tapTempo = { |now|
		now = now ? Main.elapsedTime;
		~taps = (~taps ? []).select { |t| (now - t) < 2.2 }.add(now);
		if (~taps.size >= 2) {
			var avg = (~taps.last - ~taps.first) / (~taps.size - 1);
			~setGlobal.value("bpm", (60 / avg).round);
		};
	};

	~initMIDI = {
		if (~cfg[\midi].notNil) {
			var m = ~cfg[\midi][\map];
			~midiMap = if (m.isKindOf(Array)) { m } { [] };
			~midiDevice = ~cfg[\midi][\device] ? -1;
		};
		MIDIClient.init;
		MIDIIn.connectAll;
		protect { ~sendMIDIDevices.value };

		MIDIdef.cc(\ldCC, { |val, num, chan, src|
			if (~midiAccepts.value(src)) {
				if (~learnTarget.notNil && (val > 0)) {
					var tgt = ~learnTarget;
					~learnTarget = nil;
					~midiMap = (~midiMap ? []).add((type: "cc", num: num, target: tgt));
					~bridge.sendMsg('/ldmidi', "learned", num.asInteger, tgt);
					~sendMIDIMap.value;
				} {
					(~midiMap ? []).do { |m|
						if ((m[\type] == "cc") && (m[\num] == num)) {
							~fireMIDI.value(m[\target], val);
						};
					};
				};
			};
		});

		MIDIdef.noteOn(\ldNote, { |val, num, chan, src|
			if (~midiAccepts.value(src)) {
				if (~learnTarget.notNil) {
					var tgt = ~learnTarget;
					~learnTarget = nil;
					~midiMap = (~midiMap ? []).add((type: "note", num: num, target: tgt));
					~bridge.sendMsg('/ldmidi', "learned", num.asInteger, tgt);
					~sendMIDIMap.value;
				} {
					(~midiMap ? []).do { |m|
						if ((m[\type] == "note") && (m[\num] == num)) {
							~fireMIDI.value(m[\target], 1);
						};
					};
				};
			};
		});
	};

	~sendMIDIMap = {
		var csv = (~midiMap ? []).collect { |m| "%|%|%".format(m[\type], m[\num], m[\target]) };
		~bridge.sendMsg('/ldmidimap', csv.join(";").asString);
	};
);

// -------------------------------------------------------------- osc wiring ---
(
	~port   = ~cfg[\osc][\enginePort].asInteger;
	~bridge = NetAddr("127.0.0.1", ~cfg[\osc][\statePort].asInteger);
	thisProcess.openUDPPort(~port);

	// ~tracks is created by ~boot: it is the readiness flag. Without these guards
	// a failed boot turns every UI click into a nil error flood.
	OSCdef(\ldSet, { |msg|
		if (~tracks.notNil && (msg.size > 2)) { ~applySet.value(msg[1], msg[2].asFloat) };
	}, '/ld/set', recvPort: ~port);
	OSCdef(\ldAct, { |msg|
		if (~tracks.notNil && (msg.size > 2)) { ~act.value(msg[1], msg[2]) };
	}, '/ld/act', recvPort: ~port);

	// settings / service paths ------------------------------------------------
	OSCdef(\ldSvc, { |msg|
		var what = msg[1].asString;
		// silent no-op while booting: the boot watchdog is what reports failure
		if (~tracks.notNil) {
		switch (what,
			"vstList",     { ~sendVSTList.value },
			"vstCatalog",  { if (~tracks.notNil) { ~sendVSTCatalog.value } },
			"vstOpen",   { ~vstOpen.value(msg[2].asInteger, msg[3].asInteger, msg[4].asString) },
			"vstClose",  { ~vstClose.value(msg[2].asInteger, msg[3].asInteger) },
			"vstBypass", { ~vstBypass.value(msg[2].asInteger, msg[3].asInteger, msg[4].asInteger) },
			"vstEditor", { ~vstEditor.value(msg[2].asInteger, msg[3].asInteger) },
			"midiLearn", { ~learnTarget = msg[2].asString },
			"midiMap",   { ~sendMIDIMap.value },
			"midiClear", { ~midiMap = []; ~sendMIDIMap.value },
			"midiDefault", { ~midiDefault.value },
			"midiDevice", { ~midiDevice = (msg[2] ? -1).asInteger },
			"midiDevs", { ~sendMIDIDevices.value },
			"save",      { ~saveSession.value(msg[2].asInteger) },
			"load",      { ~loadSession.value(msg[2].asInteger) },
			"export",    { ~exportMix.value(msg[2] ? 30) },
			"tap",       { ~tapTempo.value },
			"stats",     { ~sendStats.value },
			"probe",     { ~probeTrack.value((msg[2] ? 0).asInteger) },
			"clearAutosave", { ~clearAutosave.value }
		);
		};
	}, '/ld/svc', recvPort: ~port);

	// engine -> bridge --------------------------------------------------------
	// SendReply layout is [cmdName, nodeID, replyID, v1, v2, ...]. These only
	// update engine state: everything the UI needs travels in /ldstate, so there
	// is no second forwarding path to get wrong.
		
		

	// meter values: /b_setn is the reply to our /b_getn poll
	OSCdef(\ldMeterData, { |msg|
		if ((msg[1] == ~meterBuf.bufnum) && (msg[2] == 0)) {
			~meterVals = msg.copyToEnd(4);
		};
	}, '/b_setn');

	OSCdef(\ldBeat, { |msg|
		var beat = (msg[4] ? 0).asInteger;
		var beatsPerLoop = (~state[\bars] * ~state[\beatsPerBar]).max(1);
		// beat index inside the loop, counted here: a second SendReply value in the
		// same def does not survive the trip, the beat itself does
		~beatInLoop = ((~beatInLoop ? -1) + 1) % beatsPerLoop;
		~state[\phase] = ~beatInLoop / beatsPerLoop;
		~bridge.sendMsg('/ldbeat', beat);
		if (~countInLeft.notNil) {
			if (beat == 0 && (~countInStarted ? false).not) {
				~countInStarted = true;
				~countInLeft = ~state[\countIn] * ~state[\beatsPerBar];
				~transport.set(\countIn, 1);
			} {
				~countInLeft = ~countInLeft - 1;
				if (~countInLeft <= 0) {
					var p = ~pending;
					~countInLeft = nil; ~countInStarted = false; ~pending = nil;
					~transport.set(\countIn, 0);
					if (p.notNil) { ~startRec.value(p) };
				};
			};
		};
	}, '/ldbeat');

	OSCdef(\ldAuto, { |msg|
		var i = (msg[4] ? 0).asInteger, t = ~tracks[i];
		if (t.notNil && t[\autoArm] == 1 && t[\recOn] == 0) {
			t[\autoArm] = 0;
			t[\synth].set(\autoArm, 0);
			~pending = t;
			if (~state[\quantizeStart] == 0) {
				~pending = nil;
				~startRec.value(t);
			};
			~bridge.sendMsg('/ldauto', i);
		};
	}, '/ldauto');

	OSCdef(\ldWrap, {
		~beatInLoop = -1;
		if (~pending.notNil && ~countInLeft.isNil) {
			var p = ~pending;
			~pending = nil;
			~startRec.value(p);
		} {
			~tracks.do { |t|
				if (t[\recOn] == 1) {
					t[\passes] = t[\passes] + 1;
					if ((t[\layerCount] == 0) && (t[\passes] >= 1)
						&& (~state[\autoClose] == 1)) {
						~commit.value(t, true);
						// hardware behaviour: the take closes and recording carries
						// straight on as an overdub, so nothing is lost at the seam
						if ((~state[\autoDub] == 1) && (t[\layerCount] < ~maxLayers)) {
							~startRec.value(t);
						};
					};
				};
			};
			~dubPass.value;
		};
		~bridge.sendMsg('/ldwrap');
	}, '/ldwrap');

	// diagnostic: how much audio is actually in a track's first layer buffer?
	~probeTrack = { |i|
		var t = ~tracks[i];
		var peaks, pending;
		if ((t.notNil) && t[\bufs].notEmpty) {
			peaks = Array.newClear(~maxLayers);
			pending = ~maxLayers;
			t[\bufs].do { |buf, li|
				// getToFloatArray(index, count, wait, timeout, action)
				buf.getToFloatArray(0, 48000, 0, 3, { |data|
					peaks[li] = if (data.size > 0, { data.abs.maxItem }, { -1 });
					pending = pending - 1;
					if (pending == 0) {
						// one splat only: build the arg list, then send it as a bundle
						~bridge.sendBundle(nil,
							['/ldprobe'] ++ [i, t[\layerCount], t[\contentLen],
								t[\recOn], t[\playing], t[\meterL], t[\meterR]]
								++ t[\gains] ++ peaks);
					};
				});
			};
		};
	};

	~sendStats = {
		var sr = s.sampleRate;
		var lat;
		if (sr.isNil) { ^this };
		lat = ((~audio[\hardwareBufferSize] ? 256) + (~audio[\blockSize] ? 64)) / sr;
		~bridge.sendMsg('/ldstats',
			s.avgCPU,
			sr,
			(~audio[\blockSize] ? 64).asInteger,
			(~audio[\hardwareBufferSize] ? 256).asInteger,
			lat,
			if (s.serverRunning, 1, 0),
			(~hasVST == true).asInteger,
			~vstCount.value
		);
	};

	// engine -> ui state -----------------------------------------------------
	~sendState = {
		var a;
		if (~tracks.isNil) { ^this };          // not booted yet
		if (~meterVals.notNil) {
			~tracks.do { |t, i|
				t[\meterPeak] = (~meterVals[i * 4] ? 0).max(~meterVals[(i * 4) + 1] ? 0);
				t[\meterSum] = ~meterVals[(i * 4) + 2] ? 0;
			};
			~state[\masterL] = ~meterVals[60] ? 0;
			~state[\masterR] = ~meterVals[61] ? 0;
			~state[\inL] = ~meterVals[62] ? 0;
			~state[\inR] = ~meterVals[63] ? 0;
		};
		~state[\cpu] = s.avgCPU;
		a = [
			~state[\bpm], ~state[\bars], ~state[\beatsPerBar], ~state[\metro],
			~state[\countIn], ~state[\masterVol], ~state[\clickVol], ~state[\sel],
			~state[\cpu], ~state[\revMix], ~state[\room], ~state[\xfadeMs],
			~state[\recordOffset], ~state[\dubDecay], ~state[\autoClose],
			~state[\masterHp], ~state[\masterLp], ~state[\version],
			~state[\inL], ~state[\inR], ~state[\masterL], ~state[\masterR],
			~state[\phase]
		];
		~tracks.do { |t|
			a = a ++ [
				t[\state], t[\vol], t[\pan], t[\muted],
				t[\meterPeak], t[\input], t[\layerCount],
				t[\gateOn], t[\gateThr], t[\hp], t[\lp],
				t[\delayOn], t[\delayBeats], t[\delayDecay], t[\delayMix],
				t[\revSend], t[\gain], t[\recGain], t[\reverse],
				t[\fadeIn], t[\fadeOut], t[\loopRatio], t[\meterSum],
				t[\layerCount] + t[\undone]
			];
		};
		~bridge.sendMsg('/ldstate', *a);
	};

	~stateLoop = {
		fork {
			var tick = 0;
			loop {
				~sendState.value;
				tick = tick + 1;
				if (tick % 20 == 0) { ~autosaveTick.value };
				if (tick % 2 == 0) {
					// plain /b_getn: the reply lands in one persistent responder.
					// Buffer:getToFloatArray installs a timeout per call and warns
					// "streamToFloatArray failed" when replies are a beat late.
					s.sendMsg('/b_getn', ~meterBuf.bufnum, 0, 64);
				};
				0.05.wait;
			};
		};
	};

	~recoverAutosave = {
		var dir = ~autosaveDir.value;
		// session.json is written last: its presence means the save finished.
		// Without it we would recover half-written WAVs from a killed run.
		if (File.exists(dir +/+ "session.json")) {
			"recovering autosave".postln;
			~loadSessionDir.value(dir, -1);
			fork {
				2.wait;
				~autosaveSig = ~tracks.collect { |t| t[\layerCount] }.join("-");
			};
		};
	};

	// waitForBoot hangs forever when scsynth dies on startup (device busy), which
	// left the engine half-initialised and every UI click threw. Poll instead,
	// retry once, then say so.
	s.boot;
	fork {
		var attempt = 0, waited = 0;
		while { s.serverRunning.not && (waited < 8) } { 0.5.wait; waited = waited + 0.5 };
		while { s.serverRunning.not && (attempt < 2) } {
			attempt = attempt + 1;
			"*** Looper OSS: server did not start, retry %".format(attempt).postln;
			s.boot;
			waited = 0;
			while { s.serverRunning.not && (waited < 8) } { 0.5.wait; waited = waited + 0.5 };
		};
		if (s.serverRunning) {
			~boot.value;
			~recoverAutosave.value;
			~sendState.value;
			~sendMIDIMap.value;
			~stateLoop.value;
			~bridge.sendMsg('/ldready', ~nTracks);
		} {
			"*** Looper OSS: could not open the audio device. Is PipeWire/your interface busy?".postln;
			~bridge.sendMsg('/ldbootfail', "could not open the audio device");
		};
	};
);
)
