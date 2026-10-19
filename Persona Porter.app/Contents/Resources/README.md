# Persona Porter GUI — version 0.10

Swap Persona models and their UI identity between **Persona 3 FES** and
**Persona 4 on PS2**, in either direction. Uses Python 3.10+ with Tk and
the standard library. No pip packages or game assets are included.

## Start the GUI

On your Mac, unzip the package and double-click **Persona Porter.app**.
The app uses your installed Python with Tk (verified on this Mac with
Homebrew Python 3.15 and Tk 9.1). A launcher named
**Launch Persona Porter.command** is also included. Keep the script files
together if using the command launcher.

From Terminal, or on another platform with Python/Tk:

```sh
python3 persona_porter_gui.py
```

Windows installations may use `python` instead of `python3`. The GUI is
tested on this Mac; Windows and Linux have not been tested. The Mac app
wrapper does not bundle a Python interpreter or Tk runtime.

## Make a swap

1. Choose the **source ISO** (the game to import from) and **target ISO**
   (the game whose Persona slot will be replaced). Use clean original ISOs.
2. Click **Load games**. The game and Persona names are read from the ISOs.
3. Search/select a source Persona and a target slot. The previews show the
   original portrait from each game. Larger models are allowed by default.
   The optional fit filter shows only slots that need no archive expansion.
4. Choose which UI elements to transfer: name, portrait, card image and lore.
   Both battle and facility models are always transferred.
5. If the donor lore is too long, shorten it in the text box and check
   **Use this edited lore**, turn off lore transfer, or choose a larger slot.
   Custom lore supports ASCII and line breaks.
6. Optionally click **Edit gameplay…** to set base stats, resistances and
   starting/level-up skills for this target. Enable each section you want to
   apply. P3 FES provides separate **The Journey** and **The Answer** choices.
   Click **Save gameplay choices**, then **Add selected swap**. The entry saves the current UI switches, edited
   lore, larger-model setting and gameplay choices. Repeat steps 3–6 for additional Personas.
   Each target slot can appear only once; a source Persona may be reused.
7. Click **Check queue**, then **Build new ISO…** and choose a new output file. The tool writes a
   verified ISO and a matching `.manifest.json` report. Existing files are
   refused; original ISOs are opened read-only.
8. Boot the new ISO normally in PCSX2 and load an in-game save. Check the
   menu, Compendium/lore, battle and skills. Do not restore a save state.

Use **Remove selected** or **Clear queue** to manage queued entries. To change
an entry, remove it and add it again with the new choices. Draft selections
and UI/gameplay controls do not alter entries already in the queue. Changing
the target slot clears unsaved draft gameplay choices for the previous slot. The queue is kept
after a successful build and remains in memory until cleared or the app closes.

Each queue uses one source ISO and one target ISO. Clear it before loading
different ISOs or using **Reverse direction**. Each build starts from the
selected original target and includes every queued swap in one output ISO.
Names and lore are merged across all entries, including matching packed lore
copies. Each affected archive expands once for the whole batch.

Keyboard shortcuts (Command on Mac, Control elsewhere): **L** loads games,
**R** reverses direction, **A** adds the selected swap, **E** opens the gameplay editor, **Return**
checks the queue, **B** builds.
Builds run in a worker thread, with progress and cancellation. Cancellation
removes incomplete outputs. The GUI stays open until an active operation
finishes or its build is cancelled.

## What this version supports

- P3 FES **SLUS_216.21** and P4 **SLUS_217.82**, uncompressed PS2 ISO files.
  Other regions, original P3, Portable, Golden and Reload are unsupported.
- Transfers unchanged battle/facility RMD files and selected UI elements.
  Matching English lore copies inside facility bundles are updated together.
- Models that fit use the previously tested fixed-position method. Larger
  models use experimental archive expansion: affected CVMs are copied to the
  end of a new ISO, larger models are appended, and ISO/CVM metadata plus
  executable size/location records are updated. Other files in the copied
  archives are preserved and checked. This can add about 900 MiB for P3 FES
  BTL alone, or several GiB when DATA also needs expansion. The preflight
  reports the output growth. Expanded builds require in-game testing.
- Turn off **Allow larger models** to restrict builds to the earlier method.
  The fit filter is off by default, so Orpheus remains available for Izanagi.
- Lore must fit the target text buffer. Pointer values, relocation bytes,
  other dialogs, and game-specific leading text controls are preserved.
- The model reader supports the Atlus E0 extension's additional byte count.
  Unknown or inconsistent chunk layouts are still refused; model bytes are
  transferred without rewriting their internal structures.
- Some party/placeholder slots have no supported portrait or lore. Turn off
  the unavailable UI transfers where appropriate, or select another slot.
- An alternate P3 facility bundle contains a different, non-English lore
  file. It is listed in reports and left unchanged; the two matching English
  copies used by the confirmed recipe are updated.
- **Arcana, inheritance, fusion rules, voices and story scripts stay those
  of the target slot.** Level/stats, affinities and skill progression change
  only when explicitly saved in the gameplay editor. Skeleton retargeting
  and animation-slot remapping are unsupported.

Build verification checks inserted file hashes, zeroed model tails and every
byte outside the planned changes. Passing these checks establishes that the
ISO was written as planned, not that every source/target pair works in game.

## Validation status

The user confirmed P4 **Mokoi [04E] → P3 FES Orpheus [001]**, including model,
skills/animations, menu, Compendium, UI images and lore. The generalized GUI
planner reproduces all eight files from that confirmed recipe exactly.

The reverse **P3 FES Orpheus [001] → P4 Izanagi [001]** build has been checked
against both real ISOs: models, name, both images and lore match the donor;
P4's text control prefix is preserved. It still requires in-game validation.
Other combinations are also experimental. The GUI and previews have been
opened on this Mac; the automated suite contains 38 checks.

The loaded catalogs contain 190 named P3 FES slots and 214 named P4 slots.
These include some party variants; catalog presence does not establish
compatibility or availability in ordinary gameplay.

## Earlier command-line tools and format notes

The scripts below remain available for inspection and reproducing the
original development recipes. Historical build descriptions refer to the
specific version named in each section.

A foundation for investigating Persona 3 ↔ Persona 4 **PS2** model
ports. Python 3.10 or later; no packages to install. Tested with Python 3.15.
This is not yet a general model converter or a finished porting tool. Archive reading
and batch model inspection have been exercised on the user's P3 FES ISO
(`SLUS_216.21`) and Persona 4 ISO (`SLUS_217.82`). Portable, Golden and Reload are
outside this build's scope.

## Commands

Run these from the unzipped `persona_porter` folder. Replace example paths
with your actual files. JSON reports go to the terminal; redirect them to
a new report file to save them.

Inventory an ISO or an unencrypted ISO9660-based CVM:

```sh
python3 persona_porter.py inventory "/path/to/Persona 3 FES.iso" > p3_inventory.json
python3 persona_porter.py inventory "/path/to/Persona 4.iso" > p4_inventory.json
```

The report contains paths, sizes, offsets, volume metadata, and one level of
nested CVM listings. Unsupported nested archives carry an `archive_error`
instead of being silently omitted. Offsets are relative to the immediate
container, including its detected wrapper. This is not a full-disc hash or
an automatic game edition identifier.

Extract one file using the exact path from the inventory:

```sh
python3 persona_porter.py extract "/path/to/game.iso" 'DATA.CVM!PATH/FROM/INVENTORY.PAC' extracted.PAC
```

`!` enters a nested ISO/CVM. Extraction uses exclusive creation and refuses
to overwrite an existing output. A failed write can leave a partial output;
delete that partial file before retrying. PAC, PAK and BIN unpacking is not
implemented. Use this command for extracting those bundles, not files inside
them. An extracted CVM can be passed directly to `inventory` or `extract`.

Inspect and compare already extracted model files:

```sh
python3 persona_porter.py chunks source.RMD > source_chunks.json
python3 persona_porter.py compare source.RMD target.RMD > comparison.json
```

The chunk reader interprets a sequence of little-endian 12-byte headers
(`type`, payload size, raw version) and hashes each payload. It does not
descend into chunk payloads, recognize every Atlus wrapper, validate mesh
data, or infer skeleton/animation compatibility. A parse failure should be
investigated against the actual file; do not remove bytes until it parses.
Matching types and versions are **not** a compatibility verdict.

Batch-inspect Persona model sets directly from the ISO:

```sh
python3 persona_porter.py scan-models "/path/to/Persona 3 FES.iso" --archive BTL.CVM > p3_models.json
```

This scans `MODEL/PERSONA/`, `MODEL_BTL/PERSONA/` and `MODEL/FACILITYP/` by default. Use repeatable
`--prefix` arguments to select other paths. For an extracted CVM, omit
`--archive`. Each model gets its file SHA-256 and either a chunk report or an
explicit error. Matching filenames across games do not prove matching identities.

## P3 FES observations

The user's ISO has `SLUS_216.21` and `VMODE=NTSC` in `SYSTEM.CNF`.
`BGM.CVM`, `BTL.CVM` and `DATA.CVM` all expose readable ISO9660 volumes at
offset `0x1800`. Their file counts are 223, 4,355 and 7,441 respectively.

`BTL.CVM` contains 256 `MODEL/PERSONA/PSxxx.RMD` files and 256
`MODEL/FACILITYP/FPxxx.RMD` files. The use of each set remains to be verified
in game. The strict generic chunk walker reaches the end of 474 of these
512 files (234 PS, 240 FP). It reports unsupported structures for the other
38; this does not imply those original files are damaged.

Some unsupported streams become misaligned immediately after a
`0xf0f000e0` chunk. An exploratory two-byte adjustment helps some files but
does not explain all of them. No such adjustment is applied by this tool.
That format-specific length rule must be understood before any rewrite.
This is a concrete reason to preserve original bytes during the first swap.

## Experimental swap builder

`swap_copy.py` copies a target ISO and replaces explicitly selected direct RMD
files with unchanged files from a source ISO. It only allows replacements
that fit within each target file's original byte length. It updates the
target's ISO9660 directory size field (both endians), zeros the unused tail
of that file's extent, and leaves all other bytes unchanged. It never grows
the ISO or moves files. Existing output paths are refused.

Print a plan by omitting `--output`. Add `--output` to build a new ISO:

```sh
python3 swap_copy.py \
  "/path/to/Persona 3 FES.iso" \
  "/path/to/Persona 4.iso" \
  --replace 'BTL.CVM!MODEL_BTL/PERSONA/PS04E.RMD' 'BTL.CVM!MODEL/PERSONA/PS001.RMD' \
  --replace 'DATA.CVM!MODEL/FACILITYP/FP04E.RMD' 'BTL.CVM!MODEL/FACILITYP/FP001.RMD' \
  --output P3FES_Mokoi_over_Orpheus_TEST.iso > mokoi_manifest.json
```

This recipe is specific to the two observed NTSC dumps above. The P4 source
is identified as Mokoi from internal `p_mokoi` texture names; the P3 target
contains `p_orphe` texture names. The source battle/facility models are
240,283/265,819 bytes; the original target files are 314,869/321,565 bytes.
The two source variants are different and are preserved separately.

Build verification reads both inserted models back through the ISO/CVM
directories and checks their complete SHA-256 hashes against the donors.
It also checks the zeroed tails and compares every byte outside the planned
payload and directory-size ranges against the original ISO. A failed build
removes its newly created output. Verification does not prove the game will
load the models successfully. Do not modify either input during a build.

This is a visual/model test. Names, stats, skills, portraits, fusion and
gameplay tables remain those of the target slot. The model retains its own
skeleton and animations; no retargeting, slot mapping or hit-data edits are
attempted. Automatic detection of all runtime model references is not
implemented. A scan of all 530 P3 BTL PAC bundles found no entries named
`ps001` or `fp001`; that does not establish all possible runtime references.

### Testing the prepared ISO

1. Open `P3FES_Mokoi_over_Orpheus_TEST.iso` in PCSX2.
2. Boot normally and load an **in-game save**, not a save state.
3. Use a save with Orpheus available, or the normal opening sequence if needed.
4. Check the Persona/status view and summon Orpheus in battle. If the slot is
   correct and loading succeeds, its model should look like Mokoi while the
   displayed name stays Orpheus.
5. Check idle, a spell, a physical skill where available, taking damage and
   battle completion. Note which action fails and capture PCSX2 logs for crashes.

If there is no visible change, first establish that PCSX2 opened the test
ISO and that you did not restore a save state. If only one context changes,
that is evidence of another model source or a different slot mapping.

## P4 observations and cross-game baseline

P4's `SYSTEM.CNF` identifies `SLUS_217.82` and NTSC video mode.
Its BTL archive contains 256 `MODEL_BTL/PERSONA/PSxxx.RMD` files; its facility
models live in **DATA.CVM**, under `MODEL/FACILITYP/`, unlike P3 FES where
both sets are in BTL.CVM. The generic parser reaches the end of 234 P4
battle and 236 P4 facility files. Other layouts remain explicitly unsupported.

Across corresponding same-filename files in the two games, 128 battle files
and 137 facility files have identical complete SHA-256 hashes. This establishes
shared binary assets for those files, not universal model compatibility or
Persona identity for every matching numeric ID. The prepared test uses a
different source ID (P4 `04E`) over P3 `001` to make the result visible.

## Confirmed model result and new UI identity recipe

The user tested build 0.3 in PCSX2 and confirmed that Mokoi replaces Orpheus
in battle, with skills and animations working normally. That is an in-game
confirmation for this battle model recipe. The facility model was included
but has not been separately confirmed by the user.

Build 0.4 adds `mokoi_test.py` for this specific pair of NTSC-U editions.
It starts from the clean P3 FES ISO and includes both model replacements
plus six UI file changes:

| Target file | Change |
| --- | --- |
| `BTL.CVM!BATTLE/MSG.TBL` | Persona-name section, slot 001: Orpheus → Mokoi |
| `DATA.CVM!CAMP/PERSONA/PST_001.TMX` | P4 slot 04E's original 512×512 portrait |
| `DATA.CVM!CARD/PERSONA/I_PRC001.TMX` | P4 slot 04E's original 256×256 card art |
| `DATA.CVM!HELP/DATMYTH.BMD` | Slot 001's Compendium lore → P4 slot 04E's lore |
| `DATA.CVM!FACILITY/AGS_FCL_ELEVATORGIRL.PAK` | Same updated lore in embedded `../help/datMyth.bmd` |
| `DATA.CVM!FACILITY/FCL_ELEVATORGIRL.PAK` | Same updated lore in embedded `../help/datMyth.bmd` |

Both TMX headers match the P3 target headers exactly. The files are copied
byte-for-byte, preserving their original indexed palettes, pixels and alpha.
No image generation, re-encoding, background replacement or new artwork is used.
The helper `tmx_preview.py` can export these particular single-palette PSMT8
TMX files to PNG for inspection, without modifying the source.

The Persona name table is section 3 of MSG.TBL: 256 fixed-width 17-byte
records, including the NUL terminator. Only slot 001 changes. Orpheus Telos
and scripted story references are not globally renamed.

The lore is a single-page MSG1 dialog, `myth_001`; the P4 donor is
`myth_078` (decimal 78 = hex 04E). The donor's 152-byte encoded text fits
the original 175-byte text buffer. The tool preserves P3's first six text
control bytes and copies the donor's ASCII lore after its corresponding
prefix. Only the target text buffer and its length change. All pointers,
the target dialog label, file length, other dialogs and relocation bytes
remain in their original positions. Inputs with other layouts or longer
donor lore are refused rather than rebuilt.

**Build 0.5 lore correction:** The user confirmed that the Persona menu,
Compendium and battle work in 0.4, but the lore still displayed Orpheus's
description. A whole-image text search found three copies of the original
lore: the standalone HELP file and copies inside both Velvet Room
`FCL_ELEVATORGIRL` bundles. Only the standalone file had been updated in 0.4.
The observed behaviour indicates a packed copy is used in that Compendium
context; which bundle is active has not been separately isolated.

The two packed original BMDs are byte-identical to the standalone original.
Version 0.5 replaces each packed BMD with the same updated BMD. PAK member
names, sizes, offsets, other members, alignment bytes and total file size
are preserved exactly. Actual bundle padding includes nonzero bytes; the
reader preserves those rather than assuming padding is zero. No archive
repacking or relocation is involved. The packed BMD starts at `0xBAC80`
in both observed bundle files. Their runtime fix remains to be tested.

Plan or build the combined model/UI test:

```sh
python3 mokoi_test.py \
  "/path/to/Persona 3 FES.iso" \
  "/path/to/Persona 4.iso" \
  --output P3FES_Mokoi_LORE_FIX_TEST.iso > mokoi_lore_fix_manifest.json
```

Omit `--output` to print a plan. Use the **clean original** P3 ISO, not the
previous test ISO. Existing output paths are refused. Verification checks
all eight replaced files and all bytes outside their declared change ranges.

Boot the new ISO normally in PCSX2 and load an in-game save. Check the
Persona menu name/portrait and the Compendium name, description and preview;
check the card art when that slot appears in a card-related view. Summon
Mokoi and use the skills again to check for regressions. In 0.5, the main
new check is whether the lore begins with the Murngin-mythology description
instead of the Greek-poet description.

This build changes the slot's model and UI identity. Its **arcana, level,
stats, skills, affinities, fusion rules, voice lines and story scripts**
remain the P3 target's. Those require separate gameplay/script mapping;
P4 skill IDs and table records must not be blindly copied into P3.

## Safety and limitations

- Inputs are opened read-only. The separate swap builder only modifies a
  newly created output copy; there is no archive rebuilding or recompression.
- Supports 2048-byte ISO sectors and one primary ISO9660 filesystem.
- Disc/CVM volume bases are detected from sector-aligned primary descriptors
  within the first 1 MiB; no fixed CVM header size is assumed.
- No encrypted CVM directory support, raw 2352-byte disc images, UDF,
  multi-extent files, interleaving, extended attributes or multi-volume sets.
- Bounds checks prevent nested reads from escaping their parent archive.
- Large file payloads are streamed. Directory listings are parsed per record.
- Automated tests use constructed archives and chunk streams. Separately,
  the inventory and model scan were run against the user's P3 FES and P4 ISOs.

Run the checks:

```sh
python3 -m unittest -v
```

## Next development gate

1. Test build 0.5's packed Compendium lore correction in PCSX2 from an
   in-game save; verify the already confirmed UI and battle still work.
2. Resolve any additional runtime model references or behavioural links
   demonstrated by the test.
3. Understand the unsupported Atlus chunk lengths before rewriting those files.
4. Implement the skeleton, animation and packaging changes established by
   testing; expand recipes and add a user interface.
5. Test the reverse P3-to-P4 direction separately.

The supplied SMT handoff is evidence from the earlier Nocturne/Raidou project.
Its animation slots, relocation encoding, duplicate-file requirements and
ISO growth strategy are not assumed to apply to Persona without validation.

## References

- [Amicitia source and model import guide](https://github.com/tge-was-taken/Amicitia)
- [Reported P3 FES particle-list serialization issue](https://github.com/tge-was-taken/Amicitia/issues/36)
- [Reported P3 FES RWS serialization issue](https://github.com/tge-was-taken/Amicitia/issues/37)
- [Amicitia TMX implementation](https://github.com/tge-was-taken/Amicitia/blob/master/Source/AmicitiaLibrary/Graphics/TMX/TMXFile.cs)
- [Atlus Script Tools MSG1 structures](https://github.com/tge-was-taken/Atlus-Script-Tools/blob/master/Source/AtlusScriptLibrary/MessageScriptLanguage/BinaryModel/V1/Structs.cs)

Consult these implementations as references. This build contains no copied
community-tool source and no copyrighted game assets.

## Version 0.7

Adds experimental support for models larger than the target slot, in either
direction. Expansion refuses unknown CVM wrappers, extra ISO volumes, or an
ambiguous/missing executable model record before creating an output.
Cancellation removes the unfinished ISO and report. Originals are read-only.

Izanagi → Orpheus has passed structural preflight and a complete build/readback
check; it still needs an in-game test. The supplied test ISO uses a shortened
English lore description because the original Izanagi text exceeds Orpheus's
buffer. The GUI never shortens lore automatically.

## Version 0.8

Adds an in-memory batch queue in either direction: add/remove/clear entries,
per-entry UI and lore choices, duplicate-target checks, one combined preflight,
and one output ISO/report listing every swap. Shared name/lore tables and
packed copies retain all queued edits. Larger models share one expansion per
affected archive. Switching games/direction requires clearing the queue first.

A two-Persona P4 → P3 FES batch (Izanagi → Orpheus and Mokoi → Susano-o)
passed complete ISO build/readback verification, including both models per
slot, names, portraits, custom lore and both packed English lore copies.
Reverse batch expansion passed preflight. These batches need in-game testing.

## Gameplay editor (version 0.9)

Select a source Persona and target slot, then click **Edit gameplay…**
(Command-E on Mac / Control-E elsewhere). Inside the dialog, Command/Control
**1**, **2**, **3** select the stats, resistances and skills tabs; **S** saves
and **Escape** cancels. Values initially come from the
original target. Enable only the sections to change:

- **Base stats**: level and STR/MAG/END/AG/LU, each 1–99. Stat growth weights
  stay unchanged. Changing base level shifts existing learning levels by the
  same amount unless the skill list is also edited.
- **Resistances**: normal, weak, resist, null, repel or drain. P3 FES exposes
  slash/strike/pierce separately; P4 exposes physical. Almighty and ailments
  stay unchanged. Nonstandard original responses can be preserved as shown.
- **Starting / level-up skills**: searchable named selectors read from the
  target ISO, with add/update/remove controls. Choose **Starting skill** or
  an actual unlock level above the base level, up to 99. At most eight
  starting skills and 15 total entries for FES / 16 for P4. FES reserves one
  record for its end-of-progression marker. Duplicate skills are rejected.

P3 FES has separate Journey and Answer tables and different initial values.
Changing the campaign selector keeps enabled edits for the previous campaign
in this dialog; **Save gameplay choices** keeps all edited campaigns for the
current target. Unedited campaigns keep their original values.
**Reset this campaign to target** removes its edits. Cancel discards changes
made during the current dialog.

Save the choices, then **Add selected swap**. The queue captures those choices
with the model/UI swap. To revise an existing entry, remove and re-add it.
The check/build report lists gameplay changes per campaign, and the manifest
contains before/after values. This version edits regular Persona records
001–0BF; party/placeholder slots using other progression tables are refused.
It does not automatically copy source gameplay values or translate skill IDs.

Changes are written to the target edition's PERSONA/UNIT tables, preserving
file sizes, headers, stat-growth weights and fields outside the edited data.
Both supported dumps were also scanned for matching gameplay-table members
in PAK/PAC bundles; none were found.

The binary layouts were cross-checked against the public
[010 Editor format references](https://github.com/tge-was-taken/010-Editor-Templates):
[p3f_tbl.bt](https://github.com/tge-was-taken/010-Editor-Templates/blob/master/templates/p3f_tbl.bt),
[p3f_structs.bt](https://github.com/tge-was-taken/010-Editor-Templates/blob/master/templates/p3f_structs.bt),
and [p4g_tbl.bt](https://github.com/tge-was-taken/010-Editor-Templates/blob/master/templates/p4g_tbl.bt).
P4 PS2 record sizes, offsets and names were checked directly against the
local P4 dump; Golden-specific layouts are not assumed. Implementation is
original Python; no reference-template code or game assets are bundled.

Gameplay edits passed complete ISO build/readback checks in both directions,
including a forward batch with edits to both FES campaigns and archive
expansion. They still require in-game testing. Existing saved Personas and
registered Compendium entries may retain saved values. For validation, boot
the new ISO, load an in-game save, and obtain a fresh Persona through fusion
or an unregistered/default encounter rather than loading a save state or
resummoning an already-customized registered entry.

## Version 0.10

Fixes the structural check rejecting P3 FES Isis's battle model when used as
the target of a Tomoe swap. Its Atlus E0 extension includes an additional
byte-array count outside the usual declared payload size. The reader now
checks both length fields and includes those bytes in traversal and hashing.
It does not scan for replacement headers or skip unknown bytes. Generic
RenderWare streams keep the standard length rule, and inconsistent/truncated
Atlus chunks are refused. Model contents are never modified by this fix.

All 380 battle/facility files in the loaded P3 FES Persona catalog and 428
in the P4 catalog passed structural traversal, including 117 E0 blocks.
A complete P4 Tomoe (0C2) → P3 FES Isis (0C1) build passed model/UI readback
and unrelated-data preservation checks. Lore was disabled for this test:
these party slots use lore outside the supported regular-Persona layout.
The party slots' gameplay remains unchanged. This swap needs in-game testing.
