#!/usr/bin/env python3
"""Validate ELF load segments and update local Maven metadata before publication."""
import datetime as dt
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


def verify_alignment(directory):
    libraries = sorted(Path(directory).glob('jni/arm64-v8a/*.so'))
    if not libraries:
        raise ValueError('AAR has no arm64-v8a libraries')
    libraries += sorted(Path(directory).glob('jni/x86_64/*.so'))
    for library in libraries:
        output = subprocess.check_output(['readelf', '-lW', str(library)], text=True)
        loads = [line.split()[-1] for line in output.splitlines() if line.split()[:1] == ['LOAD']]
        if not loads or any(int(value, 16) < 16384 or int(value, 16) % 16384 for value in loads):
            raise ValueError(f'{library}: LOAD alignment must be a multiple of 16384; got {loads}')
        print(f'{library}: LOAD alignments {loads}')


def update_metadata(directory, version):
    if not re.fullmatch(r'[0-9][A-Za-z0-9._-]*', version):
        raise ValueError('Invalid Maven release version')
    directory = Path(directory)
    if not (directory / version / f'ffmpeg-kit-min-{version}.pom').is_file():
        raise ValueError('Versioned POM must exist before metadata is updated')
    path = directory / 'maven-metadata.xml'
    tree = ET.parse(path)
    if tree.findtext('groupId') != 'com.arthenica' or tree.findtext('artifactId') != 'ffmpeg-kit-min':
        raise ValueError('Unexpected Maven coordinates')
    versioning = tree.find('versioning')
    for tag in ['latest', 'release']:
        versioning.find(tag).text = version
    versions = versioning.find('versions')
    if version not in [item.text for item in versions]:
        ET.SubElement(versions, 'version').text = version
    updated = versioning.find('lastUpdated')
    if updated is None:
        updated = ET.SubElement(versioning, 'lastUpdated')
    updated.text = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')
    ET.indent(tree, space='  ')
    tree.write(path, encoding='utf-8', xml_declaration=True)
    for algorithm in ['md5', 'sha1', 'sha256', 'sha512']:
        path.with_suffix(path.suffix + '.' + algorithm).write_text(hashlib.new(algorithm, path.read_bytes()).hexdigest() + '\n')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'verify':
        verify_alignment(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == 'metadata':
        update_metadata(sys.argv[2], sys.argv[3])
    else:
        sys.exit('Usage: maven-artifacts.py verify EXTRACTED_AAR | metadata ARTIFACT_DIRECTORY VERSION')
