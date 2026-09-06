#!/usr/bin/env python3
import hashlib
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

M = runpy.run_path(str(Path(__file__).with_name('maven-artifacts.py')))


class ArtifactChecks(unittest.TestCase):
    def test_workflow_version_ignores_trigger_comment(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/build-16kb.yml').read_text()
        step = workflow.split('      - name: Resolve build version\n', 1)[1].split('\n      - name:', 1)[0]
        script = textwrap.dedent(step.split('        run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'maven-repo').mkdir()
            for event, value, expected in [
                ('push', '6.0-3\n# trigger Thu Apr 9 2026\n', '6.0-3'),
                ('push', '6.0-4', '6.0-4'),
                ('push', '\n# trigger\n', None),
                ('push', '../../bad\n# trigger\n', None),
                ('workflow_dispatch', '6.0-5', '6.0-5'),
                ('workflow_dispatch', '$(touch injected)', None),
            ]:
                (root / 'maven-repo/build-trigger.txt').write_text(value)
                output = root / 'output'
                output.write_text('')
                env = {**os.environ, 'REQUESTED_VERSION': value, 'GITHUB_ENV': str(root / 'env'), 'GITHUB_OUTPUT': str(output)}
                result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script.replace('${{ github.event_name }}', event)], cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, expected is not None, (event, value, result.stderr))
                self.assertEqual(output.read_text(), f'version={expected}\n' if expected else '')
                self.assertFalse((root / 'injected').exists())

    def test_load_rows_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / 'jni/arm64-v8a/libtest.so'
            library.parent.mkdir(parents=True)
            library.touch()
            with self.assertRaisesRegex(ValueError, 'x86_64'):
                M['verify_alignment'](root)
            x86_library = root / 'jni/x86_64/libtest.so'
            x86_library.parent.mkdir(parents=True)
            x86_library.touch()
            with patch('subprocess.check_output', return_value='  Type Offset Align\n  LOAD 0x0 0x4000\n  LOAD 0x0 0x4000\n'):
                M['verify_alignment'](root)
            for output in ['  Type Offset Align\n', '  LOAD 0x0 0x4000\n  LOAD 0x0 0x1000\n']:
                with patch('subprocess.check_output', return_value=output), self.assertRaises(ValueError):
                    M['verify_alignment'](root)
            metadata = root / 'maven-metadata.xml'
            metadata.write_text('<metadata><groupId>com.arthenica</groupId><artifactId>ffmpeg-kit-min</artifactId><versioning><latest>6.0-3</latest><release>6.0-3</release><versions><version>6.0-3</version></versions></versioning></metadata>')
            (root / '6.0-4').mkdir()
            (root / '6.0-4/ffmpeg-kit-min-6.0-4.pom').touch()
            for _ in range(2):
                M['update_metadata'](root, '6.0-4')
            tree = ET.parse(metadata)
            self.assertEqual(tree.findtext('versioning/latest'), '6.0-4')
            self.assertEqual(tree.findtext('versioning/release'), '6.0-4')
            self.assertRegex(tree.findtext('versioning/lastUpdated'), r'^\d{14}$')
            self.assertEqual([v.text for v in tree.findall('versioning/versions/version')], ['6.0-3', '6.0-4'])
            for algorithm in ['md5', 'sha1', 'sha256', 'sha512']:
                self.assertEqual(metadata.with_suffix('.xml.' + algorithm).read_text().strip(), hashlib.new(algorithm, metadata.read_bytes()).hexdigest())
            before = metadata.read_bytes()
            (root / '6.0-2').mkdir()
            (root / '6.0-2/ffmpeg-kit-min-6.0-2.pom').touch()
            with self.assertRaisesRegex(ValueError, 'older version'):
                M['update_metadata'](root, '6.0-2')
            self.assertEqual(metadata.read_bytes(), before)
            self.assertLess(M['release_key']('6.0-9'), M['release_key']('6.0-10'))
            self.assertLess(M['release_key']('6.0-10'), M['release_key']('6.1-1'))
            self.assertEqual(M['release_key']('6.0.0-3'), M['release_key']('6.0-3'))
            with self.assertRaises(ValueError):
                M['update_metadata'](root, '../../invalid')

    def test_candidate_cannot_stage_hooks_or_overwrite_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            incoming, repository = root / 'candidate', root / 'repository'
            incoming.mkdir()
            repository.mkdir()
            (repository / 'maven-metadata.xml').write_text('<metadata><groupId>com.arthenica</groupId><artifactId>ffmpeg-kit-min</artifactId><versioning><latest>6.0-3</latest><release>6.0-3</release><versions><version>6.0-3</version></versions></versioning></metadata>')
            for extension in ['aar', 'pom', 'module']:
                name = 'ffmpeg-kit-min-6.0-4.' + extension
                (incoming / name).write_bytes(b'candidate bytes')
                for algorithm in ['md5', 'sha1', 'sha256', 'sha512']:
                    (incoming / (name + '.' + algorithm)).write_text(hashlib.new(algorithm, b'candidate bytes').hexdigest())
            (incoming / '.git').mkdir()
            with self.assertRaises(ValueError):
                M['stage_artifacts'](incoming, repository, '6.0-4')
            (incoming / '.git').rmdir()
            aar = incoming / 'ffmpeg-kit-min-6.0-4.aar'
            aar.unlink()
            aar.symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                M['stage_artifacts'](incoming, repository, '6.0-4')
            aar.unlink()
            aar.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                M['stage_artifacts'](incoming, repository, '6.0-4')
            self.assertFalse((repository / '6.0-4').exists())
            aar.write_bytes(b'candidate bytes')
            M['stage_artifacts'](incoming, repository, '6.0-4')
            self.assertEqual(ET.parse(repository / 'maven-metadata.xml').findtext('versioning/release'), '6.0-4')
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                M['stage_artifacts'](incoming, repository, '6.0-4')


if __name__ == '__main__':
    unittest.main()
