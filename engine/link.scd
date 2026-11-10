// Ableton Link — optional, probed, never referenced from the main engine.
//
// `engine/looper.scd` loads this file only when `\LinkClock.asClass.notNil`, and
// nothing here is mentioned there by name (AGENTS invariant 15: a missing class
// aborts the block that mentions it).
//
// Link supplies a tempo and a phase reference. It is NOT the transport: the
// grid is a server-side `Phasor` on `phaseBus`, so this file polls `LinkClock`
// and nudges the existing rate control. There is no Routine-on-LinkClock
// scheduler here, because there would be nothing for it to drive.
(
	~link = nil;
	~linkBeats = 0;
	~linkPeers = 0;
	~linkPeersSent = -1;
	// 1 when Link is not in charge; the transport rate is multiplied by this
	~linkCorrection = 1;

	~linkOn = {
		if (~link.isNil) {
			// `.latency_` matters: the clock must run early by the same offset as
			// the server, or the loop lands late by exactly that much.
			// Start the session at OUR tempo: if SuperCollider is the first
			// peer it creates the session, and a constructor of 1 (beat per
			// second) would drag the looper to 60 bpm. When a session already
			// exists Link discards this in favour of the session's, which is
			// what the help file says it does.
			~link = LinkClock.new((~state[\bpm] / 60).max(0.1))
				.latency_(Server.default.latency);
			// Link's phase reference. Set once, at enable: changing the quantum
			// mid-performance makes Link change the beat duration, which is why
			// it is derived from the loop length and never touched again.
			~link.quantum = (~state[\bars] * ~state[\beatsPerBar]).max(4);
			"*** Link: joined a session (% peers, % bpm)".format(
				~link.numPeers, (~link.tempo * 60).round).postln;
		};
	};

	~linkOff = {
		if (~link.notNil) {
			~link.stop;
			~link = nil;
			~linkCorrection = 1;
			~linkPeersSent = -1;
			// leave with the tempo the player set, not with Link's: the transport
			// rate and every track's beatLen follow this
			~state[\bpm] = (~cfg[\bpm] ? 120).asFloat;
			~tracks.do { |t|
				t[\synth].set(\beatLen, ~beatSeconds.value, \loopSec, ~loopSeconds.value);
			};
			~applyRate.value;
			"*** Link: left the session".postln;
		};
	};

	// Called from ~stateLoop (~20 Hz).
	~linkTick = {
		var quantum, bpm, target, actual, err;
		if (~link.notNil) {
			// LinkClock's `tempo` is beats per SECOND; the engine's bpm is per
			// minute. Forgetting the 60 here is the classic way to get this wrong.
			bpm = ~link.tempo * 60;
			if ((bpm - ~state[\bpm]).abs > 0.05) {
				~state[\bpm] = bpm.clip(20, 300);
				~tracks.do { |t|
					t[\synth].set(\beatLen, ~beatSeconds.value,
						\loopSec, ~loopSeconds.value);
				};
			};
			// Phase: nudge the rate rather than jumping the play head, so a
			// correction can never click. `~state[\phase]` is counted per beat, so
			// the residual is at worst one beat of the loop -- say so in AUDIO.md.
			quantum = (~state[\bars] * ~state[\beatsPerBar]).max(1);
			target = (~link.beats % quantum) / quantum;
			actual = ~state[\phase] ? 0;
			err = ((target - actual + 0.5) % 1) - 0.5;
			~linkCorrection = if (err.abs < 0.004, { 1 }, { (1 + (err * 1.2)).clip(0.85, 1.15) });
			~applyRate.value;

			~linkBeats = ~link.beats;
			~linkPeers = ~link.numPeers;
			if (~linkPeers != ~linkPeersSent) {
				~linkPeersSent = ~linkPeers;
				~bridge.sendMsg('/ldlink', ~linkPeers, bpm.round(0.1), 1);
			};
		};
	};

	// no peer is not a failure: Link keeps its own tempo and says so
	~linkStatus = { |peers, bpm, on| ~bridge.sendMsg('/ldlink', peers, bpm, on) };

	"*** Link: extension present".postln;
)
