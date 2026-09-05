#!/usr/bin/env python3
"""Validate ELF load segments and update local Maven metadata before publication."""
import datetime as dt
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


def verify_alignment(directory):
    libraries = []
    for abi in ['arm64-v8a', 'x86_64']:
        abi_libraries = sorted(Path(directory).glob(f'jni/{abi}/*.so'))
        if not abi_libraries:
            raise ValueError(f'AAR has no {abi} libraries')
        libraries += abi_libraries
    for library in libraries:
        output = subprocess.check_output(['readelf', '-lW', str(library)], text=True)
        loads = [line.split()[-1] for line in output.splitlines() if line.split()[:1] == ['LOAD']]
        if not loads or any(int(value, 16) < 16384 or int(value, 16) % 16384 for value in loads):
            raise ValueError(f'{library}: LOAD alignment must be a multiple of 16384; got {loads}')
        print(f'{library}: LOAD alignments {loads}')


def release_key(version):
    # This repository publishes numeric releases such as 6.0-3; qualifiers
    # require Maven's full ordering rules and must not be guessed here.
    match = re.fullmatch(r'(\d+(?:\.\d+)*)(?:-(\d+))?', version)
    if not match:
        raise ValueError('Expected a numeric release version such as 6.0-3')
    core = tuple(map(int, match[1].split('.')))
    while core[-1:] == (0,):
        core = core[:-1]
    return core, int(match[2] or 0)


def update_metadata(directory, version):
    key = release_key(version)
    directory = Path(directory)
    if not (directory / version / f'ffmpeg-kit-min-{version}.pom').is_file():
        raise ValueError('Versioned POM must exist before metadata is updated')
    path = directory / 'maven-metadata.xml'
    tree = ET.parse(path)
    if tree.findtext('groupId') != 'com.arthenica' or tree.findtext('artifactId') != 'ffmpeg-kit-min':
        raise ValueError('Unexpected Maven coordinates')
    versioning = tree.find('versioning')
    existing = [versioning.findtext(tag) for tag in ['latest', 'release']]
    existing += [item.text for item in versioning.findall('versions/version')]
    if any(key < release_key(item) for item in existing):
        raise ValueError('Refusing to publish an older version than the current repository releases')
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


def stage_artifacts(incoming, directory, version):
    release_key(version)
    incoming, directory = Path(incoming), Path(directory)
    names = {f'ffmpeg-kit-min-{version}.{extension}' for extension in ['aar', 'pom', 'module']}
    algorithms = ['md5', 'sha1', 'sha256', 'sha512']
    expected = names | {name + '.' + algorithm for name in names for algorithm in algorithms}
    if {p.name for p in incoming.iterdir()} != expected:
        raise ValueError('Candidate must contain exactly the versioned Maven files and checksums')
    if any((incoming / name).is_symlink() or not (incoming / name).is_file() for name in expected):
        raise ValueError('Candidate entries must be regular files')
    for name in names:
        for algorithm in algorithms:
            if (incoming / (name + '.' + algorithm)).read_text().strip() != hashlib.new(algorithm, (incoming / name).read_bytes()).hexdigest():
                raise ValueError(f'Candidate checksum mismatch: {name}.{algorithm}')
    target = directory / version
    if target.exists():
        raise ValueError('Refusing to overwrite an existing Maven release')
    target.mkdir()
    for name in sorted(expected):
        shutil.copyfile(incoming / name, target / name)
    update_metadata(directory, version)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'verify':
        verify_alignment(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == 'metadata':
        update_metadata(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == 'stage':
        stage_artifacts(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit('Usage: maven-artifacts.py verify EXTRACTED_AAR | metadata ARTIFACT_DIRECTORY VERSION | stage INCOMING ARTIFACT_DIRECTORY VERSION')
