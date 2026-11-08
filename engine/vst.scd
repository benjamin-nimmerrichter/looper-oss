// Looper OSS VST insert slots.
//
// Loaded only when the VSTPlugin extension is installed: sclang cannot compile
// a function body that names a class that does not exist, so every VSTPlugin
// reference lives here and nowhere else.
(
	// ---- the insert slots inside the track chain ----------------------------
	~vstPost = { |src, bypasses|
		var out = src;
		~vstIds.do { |id, i|
			out = VSTPlugin.ar(out, 2, id: id, bypass: bypasses[i]);
		};
		out
	};

	~vstCount = { VSTPlugin.plugins(s).size ? 0 };

	~vstCatalogCSV = {
		var descs = VSTPlugin.plugins(s);
		var out = "";
		if (descs.notNil) {
			out = descs.keys.asArray.collect { |k| k.asString }.sort.join(";").asString;
		};
		out
	};

	~vstDirs = {
		var d = ~cfg[\vst][\dirs];
		var home = Platform.userHomeDir;
		var std = [
			home +/+ ".vst3", home +/+ ".local/lib/vst3", "/usr/lib/vst3",
			"/usr/local/lib/vst3", home +/+ ".vst", home +/+ ".local/lib/vst"
		];
		if (d.isNil) { std } { d.asArray ++ std }
	};

	// ---- lifecycle ----------------------------------------------------------
	~initVST = {
		~tracks.do { |t, i|
			t[\vstCtrl] = ~vstIds.collect { |id| VSTPluginController(t[\synth], id) };
		};
		~vstDirs.value.do { |dir|
			if (File.exists(dir)) {
				VSTPlugin.search(s, dir, verbose: false, action: { |info|
					~sendVSTList.value;
					~sendVSTCatalog.value;
				});
			};
		};
	};

	// ---- four FX banks per track --------------------------------------------
	// A bank is the set of keys in the four slots, nothing more: recall closes
	// whatever is loaded and opens the saved ones. Persisted by the bridge in
	// ui/config.json under vst.banks, so a bank survives a restart.
	~vstBanksInit = {
		~tracks.do { |t, i|
			var saved = (~cfg[\vst][\banks] ? [])[i];
			t[\vstBanks] = Array.fill(4, { [nil, nil, nil, nil] });
			if (saved.isKindOf(Array)) {
				saved.do { |row, b|
					if ((b < 4) && row.isKindOf(Array)) {
						t[\vstBanks][b] = Array.fill(4, { |sl|
							var k = row[sl];
							if (k.notNil && (k.asString.size > 0), { k.asString }, { nil })
						});
					};
				};
			};
		};
	};

	~sendVSTBanks = {
		var csv = ~tracks.collect { |t|
			(0..3).collect { |b|
				(t[\vstBanks][b] ? []).collect { |x| x ? "" }.join(">")
			}.join(":")
		}.join(";");
		~bridge.sendMsg('/ldvstbanks', csv.asString);
	};

	~vstBankSave = { |trackIdx, bank|
		var t = ~tracks[trackIdx];
		if (t.notNil && t[\vstBanks].notNil && (bank >= 0) && (bank < 4)) {
			t[\vstBanks][bank] = ~vstIds.collect { |id, sl|
				var k = ~vstKey.value(t, sl);
				if (k.asString.size > 0, { k.asString }, { nil })
			};
			~sendVSTBanks.value;
		};
	};

	~vstBankLoad = { |trackIdx, bank|
		var t = ~tracks[trackIdx];
		var row;
		if (t.notNil && t[\vstBanks].notNil && (bank >= 0) && (bank < 4)) {
			row = t[\vstBanks][bank];
			~vstIds.do { |id, sl| ~vstClose.value(trackIdx, sl) };
			if (row.notNil) {
				row.do { |key, sl|
					if (key.notNil && (key.asString.size > 0)) {
						~vstOpen.value(trackIdx, sl, key);
					};
				};
			};
			~sendVSTBanks.value;
		};
	};

	// ---- per slot queries ---------------------------------------------------
	// The controller is the source of truth: a cached name array went stale in
	// practice. The *key* is what open() wants ("mda Delay.vst3"); the name is
	// only for display ("mda Delay").
	~vstKey = { |t, sl|
		var k = "";
		if (t[\vstCtrl].notNil) {
			k = t[\vstCtrl][sl].info !? { |i| i.key } ? "";
		};
		k.asString
	};

	~vstName = { |t, sl|
		var nm = "";
		if (t[\vstCtrl].notNil) {
			nm = t[\vstCtrl][sl].info !? { |i| i.name } ? "";
		};
		nm.asString
	};

	// VSTPluginController keeps its own editor window private, so what we can
	// report is whether our generic parameter UI is up
	~vstWindowOpen = { |t, sl|
		var open = false;
		if (t[\vstGui].notNil) { open = t[\vstGui][sl].notNil };
		open
	};

	// ---- the state message the UI reads -------------------------------------
	// "slot|name|bypass|windowOpen" per slot, tracks separated by ";"
	~sendVSTList = {
		var csv;
		if (~tracks.isNil) { ^this };
		csv = ~tracks.collect { |t|
			~vstIds.size.collect { |sl|
				"%|%|%|%".format(sl, ~vstName.value(t, sl),
					t[\vstBypass][sl] ? 0, ~vstWindowOpen.value(t, sl).asInteger)
			}.join(",")
		}.join(";");
		~bridge.sendMsg('/ldvst', csv.asString);
	};

	~sendVSTCatalog = {
		~bridge.sendMsg('/ldvstcatalog', ~vstCatalogCSV.value);
	};

	// ---- actions ------------------------------------------------------------
	// VSTPluginController:open(path, editor, verbose, action): the action is
	// named, and it is called as action.value(controller, ok).
	~vstOpen = { |trackIdx, slot, name|
		var t = ~tracks[trackIdx];
		if (t.notNil && t[\vstCtrl].notNil) {
			t[\vstCtrl][slot].open(name.asString, action: { |ctrl, ok|
				t[\vstNames][slot] = ok.if(
					{ (ctrl.info !? { |i| i.name }) ? name.asString },
					{ "" });
				~sendVSTList.value;
			});
		};
	};

	~vstClose = { |trackIdx, slot|
		var t = ~tracks[trackIdx];
		if (t.notNil && t[\vstCtrl].notNil) {
			t[\vstCtrl][slot].close;
			t[\vstNames][slot] = "";
			t[\vstBypass][slot] = 0;
			t[\vstGui][slot] = nil;
			t[\synth].set(("vB" ++ slot).asSymbol, 0);
			~sendVSTList.value;
		};
	};

	~vstBypass = { |trackIdx, slot, on|
		var t = ~tracks[trackIdx];
		if (t.notNil && t[\synth].notNil) {
			t[\vstBypass][slot] = on.asInteger;
			t[\synth].set(("vB" ++ slot).asSymbol, on.asInteger);
			~sendVSTList.value;
		};
	};

	// A plugin with its own GUI gets that window; every other plugin gets
	// VSTPlugin's generic parameter UI, so the "ui" button is never dead.
	// Qt windows may only be created on AppClock, not from an OSC responder.
	~vstEditor = { |trackIdx, slot|
		var t = ~tracks[trackIdx];
		if (t.notNil && t[\vstCtrl].notNil) {
			var ctrl = t[\vstCtrl][slot];
			var info = ctrl.info;
			if (info.notNil) {
				if (info.hasEditor) {
					{ ctrl.editor; ~sendVSTList.value }.defer;
				} {
					{
						if (t[\vstGui][slot].isNil) {
							t[\vstGui][slot] = VSTPluginGui(ctrl);
						};
						t[\vstGui][slot].gui;    // creates the window
						~sendVSTList.value;
					}.defer;
				};
			};
		};
	};

	// ---- session persistence -------------------------------------------------
	// One line per slot ("track<tab>slot<tab>plugin key") plus one state blob
	// per loaded slot. Called from inside the session writer's Routine, so the
	// s.sync below is legal and makes writeProgram deterministic.
	~vstSaveState = { |dir|
		if (~hasVST) {
			var lines = List.new;
			File.mkdir(dir);
			~tracks.do { |t, i|
				3.do { |sl|
					var key = ~vstKey.value(t, sl);
					lines.add("%\t%\t%".format(i, sl, key));
					if (key.size > 0) {
						protect {
							t[\vstCtrl][sl].writeProgram(dir +/+ ("vst_%_%.bin".format(i, sl)))
						};
					};
				};
			};
			s.sync;                          // writeProgram goes via the server
			protect {
				var f = File(dir +/+ "vst.txt", "w");
				f.write(lines.join("\n"));
				f.close;
			};
		};
	};

	~vstLoadState = { |dir|
		var path = dir +/+ "vst.txt";
		if (~hasVST && File.exists(path)) {
			var lines = File(path, "r").readAllString.split($\n);
			lines.do { |line|
				var p = line.split($\t);
				if ((p.size >= 3) && (p[2].size > 0)) {
					~vstOpen.value(p[0].asInteger, p[1].asInteger, p[2]);
				};
			};
			// program state after the plugins have finished loading
			{
				2.5.wait;
				lines.do { |line|
					var p = line.split($\t);
					var blob;
					if ((p.size >= 3) && (p[2].size > 0)) {
						blob = dir +/+ ("vst_%_%.bin".format(p[0], p[1]));
						if (File.exists(blob)) {
							protect {
								~tracks[p[0].asInteger][\vstCtrl][p[1].asInteger]
									.readProgram(blob)
							};
						};
					};
				};
			}.fork(AppClock);
		};
	};
);
