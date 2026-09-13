#!/usr/bin/env python3
import hashlib
import json
import io
import zipfile
import os
from pathlib import Path
import runpy
import struct
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

M = runpy.run_path(str(Path(__file__).with_name('maven-artifacts.py')))


class ArtifactChecks(unittest.TestCase):
    def test_release_uses_a_draft_pr_and_preserves_main_protection(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / '.github/workflows/build-16kb.yml').read_text()
        script = textwrap.dedent(workflow.split('      - name: Prepare protected release PR\n', 1)[1].split('        run: |\n', 1)[1])
        self.assertIn('ready_for_review', (root / '.github/workflows/verify.yml').read_text())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ['git', 'gh']:
                command = directory / name
                command.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\n')
                command.chmod(0o755)
            log = directory / 'commands'
            env = {**os.environ, 'PATH': str(directory) + ':' + os.environ['PATH'],
                   'COMMAND_LOG': str(log), 'RUNNER_TEMP': str(directory),
                   'BUILD_VERSION': '6.0-4', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}
            subprocess.run(['bash', '-euo', 'pipefail', '-c', script], cwd=directory, env=env, check=True)
            calls = log.read_text()
            self.assertNotIn('HEAD:main', calls)
            self.assertIn('push origin HEAD:refs/heads/release/maven-6.0-4-123-1', calls)
            self.assertIn('pr create --draft --base main --head release/maven-6.0-4-123-1', calls)

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

    def test_build_failure_keeps_diagnostics(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/build-16kb.yml').read_text()
        step = workflow.split('      - name: Build ffmpeg-kit min variant\n', 1)[1].split('\n      - name:', 1)[0]
        self.assertIn('        shell: bash\n', step)
        script = textwrap.dedent(step.split('        run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'android.sh').write_text('#!/bin/bash\necho packaging-failed > build.log\nexit 7\n')
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7)
            self.assertIn('packaging-failed', result.stdout)
            self.assertNotIn('Build complete', result.stdout)

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
            def header(machine):
                value = bytearray(64)
                value[:7] = b'\x7fELF\x02\x01\x01'
                struct.pack_into('<HH', value, 16, 3, machine)
                return value
            library.write_bytes(header(183))
            x86_library.write_bytes(header(62))
            for invalid in [b'', header(62), b'not an ELF' + bytes(64)]:
                library.write_bytes(invalid)
                with self.assertRaisesRegex(ValueError, 'shared ELF'):
                    M['verify_alignment'](root)
            library.write_bytes(header(183))
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
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('AndroidManifest.xml', b'manifest')
                z.writestr('classes.jar', b'classes')
                for abi, machine in [('arm64-v8a', 183), ('x86_64', 62)]:
                    header = bytearray(64)
                    header[:7] = b'\x7fELF\x02\x01\x01'
                    struct.pack_into('<HH', header, 16, 3, machine)
                    z.writestr(f'jni/{abi}/libffmpegkit.so', header)
            aar_bytes = archive.getvalue()
            file_entry = {'name': 'ffmpeg-kit-min-6.0-4.aar', 'url': 'ffmpeg-kit-min-6.0-4.aar', 'size': len(aar_bytes)}
            file_entry.update({key: hashlib.new(key, aar_bytes).hexdigest() for key in ['md5', 'sha1', 'sha256', 'sha512']})
            contents = {
                'aar': aar_bytes,
                'pom': b'<project xmlns="http://maven.apache.org/POM/4.0.0"><groupId>com.arthenica</groupId><artifactId>ffmpeg-kit-min</artifactId><version>6.0-4</version><packaging>aar</packaging></project>',
                'module': json.dumps({'formatVersion': '1.1', 'component': {'group': 'com.arthenica', 'module': 'ffmpeg-kit-min', 'version': '6.0-4'}, 'variants': [{'files': [file_entry]}]}).encode(),
            }
            for extension in ['aar', 'pom', 'module']:
                name = 'ffmpeg-kit-min-6.0-4.' + extension
                (incoming / name).write_bytes(contents[extension])
                for algorithm in ['md5', 'sha1', 'sha256', 'sha512']:
                    (incoming / (name + '.' + algorithm)).write_text(hashlib.new(algorithm, contents[extension]).hexdigest())
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
            aar.write_bytes(aar_bytes)
            with patch('subprocess.check_output', return_value=' LOAD 0x0 0x0 0x0 0x1 0x1 R 0x4000'):
                M['verify_candidate_contents'](incoming, '6.0-4')
                for extension, bad in [('pom', contents['pom'].replace(b'6.0-4', b'6.0-5')),
                                       ('module', contents['module'].replace(b'"url": "ffmpeg-kit-min-6.0-4.aar"', b'"url": "https://invalid.example/other.aar"'))]:
                    path = incoming / ('ffmpeg-kit-min-6.0-4.' + extension)
                    path.write_bytes(bad)
                    with self.assertRaises(ValueError):
                        M['verify_candidate_contents'](incoming, '6.0-4')
                    path.write_bytes(contents[extension])
                M['stage_artifacts'](incoming, repository, '6.0-4')
            self.assertEqual(ET.parse(repository / 'maven-metadata.xml').findtext('versioning/release'), '6.0-4')
            with patch('subprocess.check_output', return_value=' LOAD 0x0 0x0 0x0 0x1 0x1 R 0x4000'), self.assertRaisesRegex(ValueError, 'overwrite'):
                M['stage_artifacts'](incoming, repository, '6.0-4')


if __name__ == '__main__':
    unittest.main()
