import os
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / 'launcher') not in sys.path:
    sys.path.insert(0, str(ROOT / 'launcher'))
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))
if str(ROOT / 'tests') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tests'))

import basara4_patches

try:
    import legacy
    from patches import (compile_patches, external_patches, external_selection, compile_external,
                         game_profile, selected_patches, patch_requirements, GAMES, merge_patch_files,
                         resolution_names, default_enabled)
except ImportError:
    legacy = None


SPARTAN = ROOT / 'patches/CUSA01623.xml'
ULTRAWIDE = ROOT / 'extras/mods/Ultrawide/patches/Ultrawide.xml'
SPARTAN_IDS = {'CUSA01623'}
SEGMENTS = [(0, 0x6000000)]


EXTERNAL = """<?xml version="1.0"?>
<Patch>
  <TitleID><ID>CUSA01623</ID></TitleID>
  <Metadata Title="SpartanPort" Name="On" Author="x" PatchVer="1.0" AppVer="01.02" AppElf="eboot.bin" isEnabled="true">
    <PatchList><Line Type="bytes" Address="0x00401000" Value="9090"/></PatchList>
  </Metadata>
  <Metadata Title="SpartanPort" Name="Off" Author="x" PatchVer="1.0" AppVer="01.02" AppElf="eboot.bin">
    <PatchList><Line Type="bytes32" Address="0x00402000" Value="0x12345678"/></PatchList>
  </Metadata>
  <Metadata Title="SpartanPort" Name="Mask" Author="x" PatchVer="1.0" AppVer="01.02" AppElf="eboot.bin" isEnabled="true">
    <PatchList><Line Type="mask" Value="90 ?? 90" Offset="0"/></PatchList>
  </Metadata>
  <Metadata Title="SpartanPort" Name="Old" Author="x" PatchVer="1.0" AppVer="01.00" AppElf="eboot.bin" isEnabled="true">
    <PatchList><Line Type="bytes" Address="0x00403000" Value="90"/></PatchList>
  </Metadata>
</Patch>"""


@unittest.skipIf(legacy is None, "Legacy SpartanPort patch system not available")
class ExternalPatchTests(unittest.TestCase):
    def test_selection_and_unsupported_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'extra.xml').write_text(EXTERNAL)
            (folder / 'other.xml').write_text(EXTERNAL.replace('CUSA01623', 'CUSA99999'))
            (folder / 'broken.xml').write_text('<Patch>')
            found = external_patches(folder, '01.02', SPARTAN, SPARTAN_IDS)
            self.assertEqual([key for key, _, _ in found],
                             ['extra.xml/On', 'extra.xml/Off', 'extra.xml/Mask'])
            # The file's isEnabled; the mask patch is skipped as a whole.
            writes = compile_external(external_selection(found, None), SEGMENTS)
            self.assertEqual(writes, [(0x1000, bytes.fromhex('9090'))])
            config = folder / 'patches.json'
            config.write_text('{"enabled": ["extra.xml/Off"], "disabled": ["extra.xml/On"]}')
            writes = compile_external(external_selection(found, config), SEGMENTS)
            self.assertEqual(writes, [(0x2000, (0x12345678).to_bytes(4, 'little'))])

    def test_an_older_builds_patch_file_left_by_an_update_is_not_external(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / legacy.LEGACY_PATCH_FILE).write_text(EXTERNAL)
            (folder / 'extra.xml').write_text(EXTERNAL)
            found = external_patches(folder, '01.02', SPARTAN, SPARTAN_IDS)
            self.assertEqual({key.split('/')[0] for key, _, _ in found}, {'extra.xml'})
    def test_built_in_file_is_not_external(self):
        self.assertEqual(external_patches(SPARTAN.parent, '01.02', SPARTAN, SPARTAN_IDS), [])


@unittest.skipIf(legacy is None, "Legacy SpartanPort patch system not available")
class GameProfileTests(unittest.TestCase):
    def test_profiles_by_title_id(self):
        self.assertEqual(game_profile('CUSA01623')[1:3], (SPARTAN, '01.02'))
        self.assertEqual(game_profile('CUSA01740')[1:3], (SPARTAN, '01.02'))  # the Asian release
        self.assertIsNone(game_profile('CUSA99999'))
        self.assertIsNone(game_profile(None))

    def test_unavailable_frame_rate_patch_falls_back_to_the_highest_lower_one(self):
        from graphics_options import restrict_to_available
        available = {'Bug Fix - Texture Corruption Fix', 'Frame Rate Patch - 120 FPS'}
        self.assertEqual(restrict_to_available(['Bug Fix - Texture Corruption Fix', 'Frame Rate Patch - 240 FPS'],
                                               available),
                         ['Bug Fix - Texture Corruption Fix', 'Frame Rate Patch - 120 FPS'])
        self.assertEqual(restrict_to_available(['Resolution Patch - 8K'], available), [])

    def test_spartanport_defaults_and_extras(self):
        self.assertEqual(selected_patches(SPARTAN, '01.02', ''),
                         ['Bug Fix - Texture Corruption Fix', 'Skip Any Video With X Button',
                          'Bug Fix - Startup Black Screen'])
        self.assertEqual(selected_patches(SPARTAN, '01.02', 'Resolution Patch - 720p; Skip Any Video With X Button'),
                         ['Skip Any Video With X Button', 'Bug Fix - Startup Black Screen', 'Resolution Patch - 720p'])
        self.assertEqual(selected_patches(SPARTAN, '01.00', ''), [])
        # The launcher's selection is exact: unchecked defaults stay off, except the fixes that are
        # always on (a list saved before they existed must still get them).
        self.assertEqual(selected_patches(SPARTAN, '01.02', 'Skip Any Video With X Button', only=True),
                         ['Skip Any Video With X Button', 'Bug Fix - Startup Black Screen'])
        self.assertEqual(selected_patches(SPARTAN, '01.02', '', only=True), ['Bug Fix - Startup Black Screen'])
        self.assertEqual(selected_patches(SPARTAN, '01.00', '', only=True), [])

    def test_spartanport_resolution_patch_replaces_texture_fix(self):
        self.assertEqual(selected_patches(SPARTAN, '01.02', 'Resolution Patch - 4K'),
                         ['Skip Any Video With X Button', 'Bug Fix - Startup Black Screen', 'Resolution Patch - 4K'])
        with self.assertRaises(ValueError):
            selected_patches(SPARTAN, '01.02', 'Resolution Patch - 4K;Resolution Patch - 720p')

    def test_profile_by_title_for_any_region(self):
        from unittest import mock
        import patches
        self.assertEqual(game_profile('CUSA01715')[1], SPARTAN)
        # The game's own title (param.sfo TITLE), with or without the (R) and TM marks: this is what found the
        # patches of a release under an ID that is not listed before the project was renamed to SpartanPort.
        self.assertEqual(game_profile('CUSA99999', 'God of War® III Remastered')[1], SPARTAN)
        self.assertEqual(game_profile('CUSA99999', 'God of War™ III Remastered')[1], SPARTAN)
        self.assertEqual(game_profile('CUSA99999', 'God of War III Remastered')[1], SPARTAN)
        self.assertIsNone(game_profile('CUSA99999', 'SpartanPort'))  # the project name is not a game title
        self.assertIsNone(game_profile('CUSA99999', 'Another Game'))
        with mock.patch.object(patches, 'game_sfo', return_value={'TITLE_ID': 'CUSA77777', 'TITLE': 'God of War III'}):
            self.assertTrue(patches.game_patchable('folder'))
        self.assertFalse(patches.game_patchable(None))
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(patches.game_patchable(folder))  # no param.sfo: unknown game


@unittest.skipIf(legacy is None, "Legacy SpartanPort patch system not available")
class SignatureTests(unittest.TestCase):
    XML = """<?xml version="1.0"?>
<Patch>
  <Metadata Name="Found" AppVer="01.02">
    <PatchList><Line Type="bytes" Address="0x401004" Value="9090" Pattern="11 22 ?? 44 55 66" At="1"/></PatchList>
  </Metadata>
  <Metadata Name="Call" AppVer="01.02">
    <PatchList><Line Type="bytes" Address="0x401010" Value="e8{rel:there:0}" Pattern="aa bb cc dd ee" At="0"/></PatchList>
    <Target Name="there" Address="0x401100" Pattern="e9 ?? ?? ?? ?? 77 88" At="1" Ref="1"/>
  </Metadata>
  <Metadata Name="Missing" AppVer="01.02">
    <PatchList>
      <Line Type="bytes" Address="0x401004" Value="9090" Pattern="11 22 ?? 44 55 66" At="1"/>
      <Line Type="bytes" Address="0x401020" Value="90" Pattern="de ad be ef" At="0"/>
    </PatchList>
  </Metadata>
  <Metadata Name="Data" AppVer="01.02">
    <PatchList><Line Type="bytes32" Address="0x402000" Target="variable" Value="0x2"/></PatchList>
    <Target Name="variable" Address="0x402000" Pattern="44 89 35 ?? ?? ?? ?? c6" At="3" Ref="1"/>
  </Metadata>
</Patch>"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.xml = Path(directory.name) / 'test.xml'
        self.xml.write_text(self.XML)
        image = bytearray(0x400)
        image[0x40:0x46] = bytes.fromhex('112233445566')
        image[0x80:0x85] = bytes.fromhex('aabbccddee')
        image[0x200:0x207] = bytes.fromhex('e9') + (0x300 - 0x205).to_bytes(4, 'little', signed=True) + bytes.fromhex('7788')
        # mov [rip+disp], r14d storing to 0x3f0, then the c6 of the next instruction.
        image[0x280:0x288] = bytes.fromhex('448935') + (0x3f0 - 0x287).to_bytes(4, 'little', signed=True) + b'\xc6'
        self.image = bytes(image)

    def test_sites_found_anywhere_and_rel32_recomputed(self):
        skipped = {}
        writes = compile_patches(self.xml, ['Found', 'Call', 'Missing'], None, [(0, 0x400)], self.image, skipped)
        call = (0x300 - (0x80 + 5)).to_bytes(4, 'little', signed=True)
        self.assertEqual(writes, [(0x41, b'\x90\x90'), (0x80, b'\xe8' + call)])
        # One site of the patch is missing: the whole patch is left out, with the reason.
        self.assertEqual(skipped, {'Missing': 'signature not found'})

    def test_ambiguous_signature_is_skipped(self):
        image = bytearray(self.image)
        image[0x100:0x106] = image[0x40:0x46]
        skipped = {}
        self.assertEqual(compile_patches(self.xml, ['Found'], None, [(0, 0x400)], bytes(image), skipped), [])
        self.assertEqual(skipped, {'Found': 'signature found more than once'})

    def test_without_the_image_the_reference_addresses_are_used(self):
        writes = compile_patches(self.xml, ['Found', 'Call'], '01.02', [(0, 0x10000)])
        self.assertEqual(writes[0], (0x1004, b'\x90\x90'))
        self.assertEqual(writes[1][0], 0x1010)

    def test_data_line_writes_at_its_target(self):
        # The variable has no bytes of its own to find: the code that stores to it points at it.
        self.assertEqual(compile_patches(self.xml, ['Data'], None, [(0, 0x400)], self.image, {}),
                         [(0x3f0, (2).to_bytes(4, 'little'))])
        self.assertEqual(compile_patches(self.xml, ['Data'], '01.02', [(0, 0x10000)]),
                         [(0x2000, (2).to_bytes(4, 'little'))])
        image = bytearray(self.image)
        image[0x280:0x288] = bytes(8)  # store not found: the whole patch is left out
        skipped = {}
        self.assertEqual(compile_patches(self.xml, ['Data'], None, [(0, 0x400)], bytes(image), skipped), [])
        self.assertEqual(skipped, {'Data': 'signature not found'})

    def test_signatures_match_the_full_update_eboot(self):
        # Without that eboot.bin here: the original bytes others published for it must be the
        # fixed bytes of each signature at its site, with one shift per code region.
        import json
        import re
        known = json.loads((ROOT / 'tests/data/eboot_6e72_sites.json').read_text())
        fixed = lambda pattern, at, n: [None if b == '??' else int(b, 16) for b in pattern.split()][at:at + n]
        shifts = {}
        metas = {m.get('Name'): list(m.iter('Line')) for m in ET.parse(SPARTAN).getroot().iter('Metadata')}
        for name, sites in known['patches'].items():
            for index, address, original in sites:
                line = metas[name][index]
                ours = fixed(line.get('Pattern'), int(line.get('At')), len(original) // 2)
                self.assertTrue(all(b is None or b == o for b, o in zip(ours, bytes.fromhex(original))),
                                f'{name} line {index}')
                reference = int(line.get('Address'), 0)
                shifts.setdefault(reference >> 16, set()).add(int(address, 0) - reference)
        self.assertTrue(all(len(s) == 1 for s in shifts.values()), shifts)
        header = (ROOT / 'src/cheat_hook.h').read_text()
        hooks = re.findall(r'\{(0x[0-9a-f]+),SPARTAN_CHEAT_\w+,\d+,\d+,\s*\{([^}]*)\},.*?\{"([^"]+)",(\d+)\}\}',
                           header, re.S)
        for name, (address, original) in known['cheats'].items():
            # The cheats' region moved -0x10 (Rage of Sparta has two sites with the same bytes).
            match = [h for h in hooks if bytes(int(b, 0) for b in h[1].split(',')).hex() == original
                     and int(h[0], 0) - 0x10 == int(address, 0)]
            self.assertEqual(len(match), 1, name)
            _, _, pattern, at = match[0]
            self.assertEqual(fixed(pattern, int(at), len(original) // 2), list(bytes.fromhex(original)), name)

    @unittest.skipUnless(os.environ.get('SPARTAN_GAME'), 'SPARTAN_GAME (game folder) not set')
    def test_spartanport_signatures_in_the_reference_and_a_moved_build(self):
        from prepare import parse_self
        from patches import eboot_image, eboot_segments
        elf, *_ = parse_self((Path(os.environ['SPARTAN_GAME']) / 'eboot.bin').read_bytes())
        image = eboot_image(bytes(elf))
        names = [m.get('Name') for m in ET.parse(SPARTAN).getroot().iter('Metadata')]
        segments = eboot_segments(bytes(elf))  # as patches.py: .bss included (memsz)
        # The reference build: the signatures give exactly the Address writes.
        for name in names:
            self.assertEqual(compile_patches(SPARTAN, [name], None, segments, image, {}),
                             compile_patches(SPARTAN, [name], '01.02', segments), name)
        # A build whose code moved like the full 01.02 update's: +0x120 from 0x1f0000 to 0x210000,
        # data unchanged (the bytes taken out keep the data at its address).
        moved = image[:0x1f0000] + bytes(0x120) + image[0x1f0000:0x210000] + image[0x210120:]
        self.assertEqual(len(moved), len(image))
        shift = lambda at: at + 0x120 if 0x1f0000 <= at < 0x210000 else at
        for name in ('Resolution Patch - 4K', 'Resolution Patch - 8K', 'Skip Intro'):
            reference = compile_patches(SPARTAN, [name], None, segments, image, {})
            found = compile_patches(SPARTAN, [name], None, segments, moved, {})
            self.assertEqual([at for at, _ in found], [shift(at) for at, _ in reference], name)
        # Skip Intro's cave moved: the boot call into it is recomputed, the flag (data) is not.
        writes = dict(compile_patches(SPARTAN, ['Skip Intro'], None, segments, moved, {}))
        rel = lambda data, at, end: end + int.from_bytes(data[at:at + 4], 'little', signed=True)
        self.assertEqual(rel(writes[0x01b36a], 1, 0x01b36f), shift(0x212170))
        flag = rel(writes[0x0c8440], 2, 0x0c844a)  # the getter (not moved) and the cave share it
        self.assertEqual(rel(writes[shift(0x212170)], 2, shift(0x212170) + 10), flag)

    def test_spartanport_patches_compile(self):
        names = [m.get('Name') for m in ET.parse(SPARTAN).getroot().iter('Metadata')]
        self.assertTrue(compile_patches(SPARTAN, names, '01.02', SEGMENTS))

    def test_spartanport_startup_black_screen(self):
        # The frame index variable (.bss) starts at 2, the index before the first frame's 0.
        self.assertEqual(compile_patches(SPARTAN, ['Bug Fix - Startup Black Screen'], '01.02', SEGMENTS),
                         [(0x14ba600, (2).to_bytes(4, 'little'))])

    def test_spartanport_skip_intro_targets(self):
        # Ported from the other 01.02 build: every jump must land where this eboot has the code.
        writes = dict(compile_patches(SPARTAN, ['Skip Intro'], '01.02', SEGMENTS))
        rel = lambda data, at, end: end + int.from_bytes(data[at:at + 4], 'little', signed=True)
        self.assertEqual(writes[0x184950], bytes.fromhex('eb34'))  # jne over the logo start
        getter = writes[0x0c8440]  # mov [flag], 1; mov eax, 1; ret
        self.assertEqual(len(getter), 16)
        self.assertEqual(rel(getter, 2, 0x4c844a), 0x1e79258)
        call = writes[0x01b36a]  # the boot call now goes through the cave
        self.assertEqual(rel(call, 1, 0x41b36f), 0x612170)
        cave = writes[0x212170]  # mov [flag], 1; jmp to the original callee
        self.assertEqual(len(cave), 15)
        self.assertEqual(rel(cave, 2, 0x61217a), 0x1e79258)
        self.assertEqual(rel(cave, 11, 0x61217f), 0x4b4c90)

    @unittest.skipUnless(os.environ.get('SPARTAN_GAME'), 'SPARTAN_GAME (game folder) not set')
    def test_spartanport_skip_intro_original_bytes(self):
        from prepare import parse_self
        elf, _, ph, _, _ = parse_self((Path(os.environ['SPARTAN_GAME']) / 'eboot.bin').read_bytes())
        text = next(p for p in ph if p['type'] == 1 and p['flags'] & 1)
        original = {0x184950: '7534', 0x0c8440: '488d05955b38008b00c3660f1f440000',
                    0x01b36a: 'e821990900', 0x212170: '90' * 15}
        for offset, value in original.items():
            at = text['offset'] + offset - text['vaddr']
            self.assertEqual(bytes(elf[at:at + len(value) // 2]).hex(), value, hex(offset))

    def test_8k_patch_dimensions_and_memory(self):
        name = 'Resolution Patch - 8K'
        writes = dict(compile_patches(SPARTAN, [name], '01.02', SEGMENTS))
        expected = {0x55a876: (0x9090, 2),
                    0x5f0b3b: (7680, 4), 0x5f0b43: (4320, 4),
                    0x5f0b4c: (7680, 2), 0x5f0b53: (4320, 2),
                    0x5643f3: (4320, 4), 0x564400: (7680, 4),
                    0x5ff769: (7680, 4), 0x5ff76f: (4320, 4),
                    0x7c46b4: (7680, 4), 0x7c46b8: (4320, 4),
                    0x56bb82: (4320, 4), 0x60953d: (4320, 4),
                    0x7f2a3c: (3840, 4), 0x7f2a40: (2160, 4),
                    0x7cd918: (0xff000000, 4)}
        self.assertEqual(writes, {address - 0x400000: value.to_bytes(size, 'little')
                                  for address, (value, size) in expected.items()})
        self.assertEqual(patch_requirements(SPARTAN, [name], '01.02'), (5056 + 6144, None))

    def test_requirements_from_patch_notes(self):
        # Notes ask for extra direct memory (shadPS4 "DMEM") and a VBlank rate.
        self.assertEqual(patch_requirements(SPARTAN, ['Resolution Patch - 4K', 'Skip Any Video With X Button'], '01.02'),
                         (5056 + 6144, None))
        self.assertEqual(patch_requirements(SPARTAN, ['Frame Rate Patch - 120 FPS'], '01.02'), (None, 120))
        self.assertEqual(patch_requirements(SPARTAN, ['Frame Rate Patch - 144 FPS'], '01.02'), (None, 144))
        self.assertEqual(patch_requirements(SPARTAN, [], '01.02'), (None, None))

    def test_144_frame_rate_patch_has_the_reference_sites_and_value(self):
        metadata = next(m for m in ET.parse(SPARTAN).getroot().iter('Metadata')
                        if m.get('Name') == 'Frame Rate Patch - 144 FPS')
        lines = list(metadata.find('PatchList'))
        self.assertEqual([line.get('Address') for line in lines],
                         ['0x0048ff1a', '0x0075dbe4'])
        self.assertEqual(lines[1].get('Value'), '00001043')
        self.assertIn('VBlank to 144 FPS', metadata.get('Note'))



@unittest.skipIf(legacy is None, "Legacy SpartanPort patch system not available")
class ModPatchTests(unittest.TestCase):
    MOD = """<?xml version="1.0"?>
<Patch>
  <TitleID><ID>CUSA01623</ID></TitleID>
  <Metadata Name="Resolution Patch - 3440x1440" Note="Requires setting DMEM to 4608 MB or more." AppVer="01.02" AppElf="eboot.bin" isEnabled="false">
    <PatchList><Line Type="bytes" Address="0x00401000" Value="9090"/></PatchList>
  </Metadata>
  <Metadata Name="Resolution Patch - 2560x1080" AppVer="01.02" AppElf="eboot.bin" isEnabled="false">
    <PatchList><Line Type="bytes" Address="0x00401000" Value="9090"/></PatchList>
  </Metadata>
  <Metadata Name="Skip Any Video With X Button" Note="replaced by the mod" AppVer="01.02" AppElf="eboot.bin" isEnabled="true">
    <PatchList><Line Type="bytes" Address="0x00401002" Value="90"/></PatchList>
  </Metadata>
</Patch>"""
    OTHER_GAME = '<Patch><TitleID><ID>CUSA99999</ID></TitleID><Metadata Name="Elsewhere" AppVer="01.02"><PatchList/></Metadata></Patch>'

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.mod = self.dir / 'mod.xml'
        self.mod.write_text(self.MOD)

    def test_resolution_names_of_patch_files_smallest_first(self):
        self.assertEqual(resolution_names([self.mod]), ['2560x1080', '3440x1440'])
        self.assertEqual(resolution_names([self.dir / 'missing.xml']), [])

    def test_merge_adds_replaces_and_skips_other_titles(self):
        other = self.dir / 'other.xml'
        other.write_text(self.OTHER_GAME)
        merged = merge_patch_files(SPARTAN, [self.mod, other], self.dir / 'out' / 'merged.xml')
        names = [m.get('Name') for m in ET.parse(merged).getroot().iter('Metadata')]
        self.assertIn('Resolution Patch - 3440x1440', names)
        self.assertNotIn('Elsewhere', names)
        self.assertEqual(names.count('Skip Any Video With X Button'), 1)  # replaced, not repeated
        note = next(m.get('Note') for m in ET.parse(merged).getroot().iter('Metadata')
                    if m.get('Name') == 'Skip Any Video With X Button')
        self.assertEqual(note, 'replaced by the mod')
        # The built-in patches are all still there, and the merged file is selectable like the original.
        self.assertTrue(set(m.get('Name') for m in ET.parse(SPARTAN).getroot().iter('Metadata')) - {'Skip Any Video With X Button'} <= set(names))
        chosen = selected_patches(merged, '01.02', 'Resolution Patch - 3440x1440', only=True)
        self.assertIn('Resolution Patch - 3440x1440', chosen)  # (the startup fix is always added)
        self.assertNotIn('Resolution Patch - 2560x1080', chosen)
        self.assertEqual(patch_requirements(merged, ['Resolution Patch - 3440x1440'], '01.02'), (5056 + 4608, None))

    def test_a_broken_mod_file_is_left_out(self):
        broken = self.dir / 'broken.xml'
        broken.write_text('<Patch><Metadata')
        merged = merge_patch_files(SPARTAN, [broken, self.mod], self.dir / 'merged.xml')
        self.assertIn('Resolution Patch - 2560x1080',
                      [m.get('Name') for m in ET.parse(merged).getroot().iter('Metadata')])

    def test_default_enabled_patches_of_a_mod(self):
        self.assertEqual(default_enabled([self.mod], '01.02'), ['Skip Any Video With X Button'])
        self.assertEqual(default_enabled([self.mod], '01.00'), [])

    # --- the Ultrawide mod (extras/mods/Ultrawide): ULTRAWIDE sizes -> (extra direct memory MiB, k)
    SIZES = {
        (2560, 1080): (3072, (2560 / 1080) / (16 / 9)),
        (3440, 1440): (4608, (3440 / 1440) / (16 / 9)),
        (3840, 1600): (6144, (3840 / 1600) / (16 / 9)),
        (5120, 1440): (6144, 2.0),
        (5120, 2160): (6144, (5120 / 2160) / (16 / 9)),
    }

    # 16:10, taller than 16:9: only the resolution and the aspect ratio (the camera's fixed horizontal view
    # already shows more at the top and bottom), so no field of view change.
    TALLER = {(1920, 1200): 3072, (2560, 1600): 4608, (1280, 800): 3072,
              (1440, 900): 3072, (1360, 768): 3072,
              (1280, 960): 3072, (1440, 1080): 3072, (1920, 1440): 3072, (2048, 1536): 3072, (2560, 1920): 4608}

    def test_the_ultrawide_mod_lists_its_resolutions_and_memory_needs(self):
        self.assertEqual(resolution_names([ULTRAWIDE]), ['1280x800', '1360x768', '1280x960', '1440x900', '1440x1080', '1920x1200',
                                                         '1920x1440', '2560x1080', '2048x1536', '2560x1600', '2560x1920', '3440x1440',
                                                         '3840x1600', '5120x1440', '5120x2160'])
        for (width, height), dmem in self.TALLER.items():
            name = f'Resolution Patch - {width}x{height}'
            self.assertEqual(patch_requirements(ULTRAWIDE, [name], '01.02'), (5056 + dmem, None), name)
        for (width, height), (dmem, _k) in self.SIZES.items():
            name = f'Resolution Patch - {width}x{height}'
            self.assertEqual(patch_requirements(ULTRAWIDE, [name], '01.02'), (5056 + dmem, None), name)

    def test_the_ultrawide_mod_camera_caves_form_closed_chains(self):
        # Each entry (its Address values are the 6e72 build's): the four camera sites jump into two chained caves.
        # The first runs the instruction the jump replaced, the second multiplies the field of view tangent by
        # k = (width / height) / (16 / 9) and returns right after the site.
        rel = lambda data, at, end: end + int.from_bytes(data[at:at + 4], 'little', signed=True)
        for (width, height), (_dmem, k) in self.SIZES.items():
            name = f'Resolution Patch - {width}x{height}'
            writes = dict(compile_patches(ULTRAWIDE, [name], '01.02', SEGMENTS))
            self.assertEqual(writes[0x1f0c77], struct.pack('<f', width / height) * 2, name)  # the aspect ratio
            constant = [at for at, data in writes.items() if data == struct.pack('<f', k)]
            self.assertEqual(len(constant), 1, name)
            for site, original in ((0x1cc01f, 'c5fa1089d8000000'), (0x1cbd89, 'c5fa1080d8000000'),
                                   (0x1ccaa9, 'c5fa1080d8000000'), (0x1cc20f, 'c5fa5e852cffffff')):
                jump = writes[site]
                self.assertEqual((jump[0], jump[5:]), (0xe9, bytes.fromhex('0f1f00')), name)  # jmp + 3-byte nop
                first = rel(jump, 1, site + 5)
                self.assertEqual(writes[first][:8], bytes.fromhex(original), name)
                self.assertEqual(writes[first][8], 0xe9, name)
                second = rel(writes[first], 9, first + 13)
                cave = writes[second]
                self.assertEqual((cave[0], cave[2], cave[8]), (0xc5, 0x59, 0xe9), name)  # vmulss ..., [rip+k]; jmp
                self.assertEqual(rel(cave, 4, second + 8), constant[0], name)
                self.assertEqual(rel(cave, 9, second + 13), site + 8, name)

    def test_taller_than_16_9_changes_no_camera(self):
        for (width, height) in self.TALLER:
            writes = dict(compile_patches(ULTRAWIDE, [f'Resolution Patch - {width}x{height}'], '01.02', SEGMENTS))
            aspect = struct.pack('<f', width / height)
            # the aspect ratio, twice; for 4:3 the second float is one unit above 4/3, which the game's letterbox
            # test (function at 0x56fc60, "== 4/3") does not match
            second = struct.pack('<I', struct.unpack('<I', aspect)[0] + 1) if aspect == struct.pack('<f', 4 / 3) else aspect
            self.assertEqual(writes[0x1f0c77], aspect + second)
            # the resolution (these lines keep the built-in entries' Address values, the reference build's)
            self.assertEqual(writes[0x1f0b3b], struct.pack('<I', width))
            self.assertEqual(writes[0x1f0b43], struct.pack('<I', height))
            for site in (0x1cc01f, 0x1cbd89, 0x1ccaa9, 0x1cc20f):  # no camera site, no cave
                self.assertNotIn(site, writes)
            self.assertEqual(len(writes), 15 + 1)  # the resolution lines (15) and the aspect ratio

    def test_the_ultrawide_mod_is_off_until_a_resolution_is_chosen(self):
        self.assertEqual(default_enabled([ULTRAWIDE], '01.02'), [])



@unittest.skipIf(legacy is None, "Legacy SpartanPort patch system not available")
class HalfResolutionTests(unittest.TestCase):
    """The half-resolution post-processing buffer (a 960x540 table at 0x7f2a3c) must be half of the
    patched output, or a smaller output gets a larger buffer and overexposed lights (#390)."""

    def test_every_resolution_patch_halves_the_output(self):
        size = lambda value: int.from_bytes(bytes.fromhex(value), 'little')
        for meta in ET.parse(SPARTAN).getroot().iter('Metadata'):
            if not meta.get('Name').startswith('Resolution Patch'):
                continue
            lines = {line.get('Address'): line.get('Value') for line in meta.iter('Line')}
            width, height = size(lines['0x005f0b3b']), size(lines['0x005f0b43'])
            half = (lines.get('0x007f2a3c'), lines.get('0x007f2a40'))
            self.assertTrue(all(half), meta.get('Name'))
            self.assertEqual((size(half[0]), size(half[1])), (width // 2, height // 2), meta.get('Name'))

def make_mock_elf(payload, vaddr=0x1000):
    phoff = 0x40
    phentsize = 56
    phnum = 1
    data_offset = 0x100
    elf = bytearray(data_offset + len(payload))
    struct.pack_into('<Q', elf, 0x20, phoff)
    struct.pack_into('<HH', elf, 0x36, phentsize, phnum)
    struct.pack_into('<IIQQQQQQ', elf, phoff, 1, 7, data_offset, vaddr, vaddr, len(payload), len(payload), 0x1000)
    elf[data_offset:data_offset + len(payload)] = payload
    return bytes(elf)


class Basara4VerticalSplitPatchTests(unittest.TestCase):
    SPLIT_TABLE_BYTES = bytes.fromhex(basara4_patches.SPLIT_TABLE)
    ASPECT_SITE_BYTES = bytes.fromhex(basara4_patches.ASPECT_SITE)
    HUD_P2_SITE_BYTES = bytes.fromhex("c4 c1 7a 10 57 54 c5 ea 58 15 45 6e e9 00 c4 c1 7a 58 47 58")

    def test_vertical_splitscreen_finds_site_and_returns_correct_patch(self):
        site_table = 0xd90b50
        site_aspect = 0x200000
        site_hud = 0x21fa80
        total_len = max(site_table + len(self.SPLIT_TABLE_BYTES),
                        site_aspect + len(self.ASPECT_SITE_BYTES),
                        site_hud + len(self.HUD_P2_SITE_BYTES)) + 0x100
        image = bytearray(total_len)
        image[site_table:site_table + len(self.SPLIT_TABLE_BYTES)] = self.SPLIT_TABLE_BYTES
        image[site_aspect:site_aspect + len(self.ASPECT_SITE_BYTES)] = self.ASPECT_SITE_BYTES
        image[site_hud:site_hud + len(self.HUD_P2_SITE_BYTES)] = self.HUD_P2_SITE_BYTES

        writes = basara4_patches.vertical_splitscreen(bytes(image))
        self.assertEqual(len(writes), 3)

        # 1. Viewport jump table redirect
        offset_table, data_table = writes[0]
        self.assertEqual(offset_table, site_table + 8)
        self.assertEqual(data_table, b'\x14\xff\xff\xff\x32\xff\xff\xff')

        # Verify displacements decode correctly
        # Mode 2 entry is patched to Mode 4 displacement (-236)
        disp_mode4, = struct.unpack_from('<i', data_table, 0)
        self.assertEqual(disp_mode4, -236)
        # Mode 3 entry is patched to Mode 5 displacement (-206)
        disp_mode5, = struct.unpack_from('<i', data_table, 4)
        self.assertEqual(disp_mode5, -206)

        # 2. Camera aspect ratio un-stretch
        offset_aspect, data_aspect = writes[1]
        self.assertEqual(offset_aspect, site_aspect + 8)
        self.assertEqual(data_aspect, b'\xc5\xf8\x2e\xd0\x76\x04\xc5\xfa\x58\xc0\xc3')

        # 3. Player 2 HUD reposition to Monitor 2
        offset_hud, data_hud = writes[2]
        self.assertEqual(offset_hud, site_hud + 6)
        self.assertEqual(data_hud, b'\xc5\xf2\x58\x0d\xa1\x07\x0a\x01')

    def test_vertical_splitscreen_missing_raises_lookup_error(self):
        dummy_image = b'\x90' * 0x1000
        with self.assertRaises(LookupError) as ctx:
            basara4_patches.vertical_splitscreen(dummy_image)
        self.assertIn('split table not found', str(ctx.exception))

        # Split table present, aspect missing
        image_no_aspect = bytearray(0x2000)
        image_no_aspect[0x100:0x100 + len(self.SPLIT_TABLE_BYTES)] = self.SPLIT_TABLE_BYTES
        with self.assertRaises(LookupError) as ctx:
            basara4_patches.vertical_splitscreen(bytes(image_no_aspect))
        self.assertIn('aspect site not found', str(ctx.exception))

        # Split table & aspect present, hud missing
        image_no_hud = bytearray(0x2000)
        image_no_hud[0x100:0x100 + len(self.SPLIT_TABLE_BYTES)] = self.SPLIT_TABLE_BYTES
        image_no_hud[0x200:0x200 + len(self.ASPECT_SITE_BYTES)] = self.ASPECT_SITE_BYTES
        with self.assertRaises(LookupError) as ctx:
            basara4_patches.vertical_splitscreen(bytes(image_no_hud))
        self.assertIn('hud site not found', str(ctx.exception))

    def test_vertical_splitscreen_duplicate_raises_lookup_error(self):
        image = b'AAA' + self.SPLIT_TABLE_BYTES + b'BBB' + self.SPLIT_TABLE_BYTES
        with self.assertRaises(LookupError) as ctx:
            basara4_patches.vertical_splitscreen(image)
        self.assertIn('split table found twice', str(ctx.exception))

        # Duplicate aspect site
        valid_table_and_hud = bytearray(0x2000)
        valid_table_and_hud[0x100:0x100 + len(self.SPLIT_TABLE_BYTES)] = self.SPLIT_TABLE_BYTES
        valid_table_and_hud[0x200:0x200 + len(self.ASPECT_SITE_BYTES)] = self.ASPECT_SITE_BYTES
        valid_table_and_hud[0x300:0x300 + len(self.ASPECT_SITE_BYTES)] = self.ASPECT_SITE_BYTES
        with self.assertRaises(LookupError) as ctx:
            basara4_patches.vertical_splitscreen(bytes(valid_table_and_hud))
        self.assertIn('aspect site found twice', str(ctx.exception))

    def test_build_respects_vertical_split_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            elf_path = tmp / 'eboot.elf'
            out_path = tmp / 'patches.bin'

            split_payload = (self.SPLIT_TABLE_BYTES + b'\x90' * 0x20 +
                             self.ASPECT_SITE_BYTES + b'\x90' * 0x20 +
                             self.HUD_P2_SITE_BYTES)
            payload = b'\x90' * 0x200 + split_payload + b'\x90' * 0x200
            elf_bytes = make_mock_elf(payload, vaddr=0x4000)
            elf_path.write_bytes(elf_bytes)

            # Test 1: vertical_split=False -> no patches written
            writes, notes = basara4_patches.build(elf_path, out_path, unlimited=False, vertical_split=False)
            self.assertEqual(writes, [])
            self.assertNotIn('vertical split', notes)
            self.assertTrue(out_path.is_file())
            # Header check: G3PATCH2 with 0 count
            blob = out_path.read_bytes()
            magic, base, count = struct.unpack_from('<8sQQ', blob, 0)
            self.assertEqual(magic, b'G3PATCH2')
            self.assertEqual(count, 0)

            # Test 2: vertical_split=True -> all 3 vertical split patches included
            writes, notes = basara4_patches.build(elf_path, out_path, unlimited=False, vertical_split=True)
            self.assertEqual(len(writes), 3)
            self.assertIn('vertical split', notes)
            expected_table = 0x4000 + 0x200
            expected_aspect = expected_table + len(self.SPLIT_TABLE_BYTES) + 0x20
            expected_hud = expected_aspect + len(self.ASPECT_SITE_BYTES) + 0x20
            self.assertEqual(writes[0], (expected_table + 8, b'\x14\xff\xff\xff\x32\xff\xff\xff'))
            self.assertEqual(writes[1], (expected_aspect + 8, b'\xc5\xf8\x2e\xd0\x76\x04\xc5\xfa\x58\xc0\xc3'))
            self.assertEqual(writes[2], (expected_hud + 6, b'\xc5\xf2\x58\x0d\xa1\x07\x0a\x01'))

            # Check binary patches.bin format
            blob = out_path.read_bytes()
            magic, base, count = struct.unpack_from('<8sQQ', blob, 0)
            self.assertEqual(magic, b'G3PATCH2')
            self.assertEqual(base, basara4_patches.EBOOT_BASE)
            self.assertEqual(count, 3)
            rec_off, rec_len = struct.unpack_from('<QQ', blob, 24)
            self.assertEqual(rec_off, expected_table + 8)
            self.assertEqual(rec_len, 8)
            self.assertEqual(blob[40:48], b'\x14\xff\xff\xff\x32\xff\xff\xff')

    def test_build_handles_both_patches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            elf_path = tmp / 'eboot.elf'
            out_path = tmp / 'patches.bin'

            limiter_code = bytes.fromhex(
                "c5 e2 5c d0 c5 f2 59 ca c5 f8 2e 0d 11 22 33 44 72 20 c4 e1 fa 2c c1 69 f8 e8 03 00 00 "
                "81 c7 18 fc ff ff e8 55 66 77 88 c5 fa 10 5d cc c5 fa 10 43 60 c5 f8 2e d8 "
                "c5 fb 10 4d d0 0f 86 10 00 00 00"
            )
            split_payload = (self.SPLIT_TABLE_BYTES + b'\x90' * 0x20 +
                             self.ASPECT_SITE_BYTES + b'\x90' * 0x20 +
                             self.HUD_P2_SITE_BYTES)
            payload = limiter_code + b'\x90' * 0x100 + split_payload
            elf_bytes = make_mock_elf(payload, vaddr=0x10000)
            elf_path.write_bytes(elf_bytes)

            writes, notes = basara4_patches.build(elf_path, out_path, unlimited=True, vertical_split=True)
            self.assertEqual(len(writes), 5)  # 2 for limiter + 3 for split
            self.assertIn('unlimited fps', notes)
            self.assertIn('vertical split', notes)

    def test_launcher_defaults_and_row_labels(self):
        import basara4_launcher
        self.assertIn("vertical_splitscreen", basara4_launcher.DEFAULTS)
        self.assertFalse(basara4_launcher.DEFAULTS["vertical_splitscreen"])
        self.assertIn("vertical_splitscreen", basara4_launcher.ROW_EN)
        self.assertEqual(basara4_launcher.ROW_EN["vertical_splitscreen"][0], "Vertical split-screen (Dual monitor)")
        self.assertIn("vertical_splitscreen", basara4_launcher.ROW_ID)
        self.assertEqual(basara4_launcher.ROW_ID["vertical_splitscreen"][0], "Split-screen vertikal (Dual monitor)")


if __name__ == '__main__':
    unittest.main()

