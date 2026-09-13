#!/usr/bin/env python3
"""Validate ELF load segments and update local Maven metadata before publication."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET


def verify_alignment(directory):
    libraries = []
    for abi in ['arm64-v8a', 'x86_64']:
        abi_libraries = sorted(Path(directory).glob(f'jni/{abi}/*.so'))
        if not abi_libraries:
            raise ValueError(f'AAR has no {abi} libraries')
        libraries += abi_libraries
    for library in libraries:
        with library.open('rb') as source:
            header = source.read(64)
        machine = {'arm64-v8a': 183, 'x86_64': 62}[library.parent.name]
        if (len(header) < 64 or header[:7] != b'\x7fELF\x02\x01\x01' or
                struct.unpack_from('<HH', header, 16) != (3, machine)):
            raise ValueError(f'{library}: expected a 64-bit little-endian shared ELF for {library.parent.name}')
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


def verify_candidate_contents(incoming, version):
    stem = f'ffmpeg-kit-min-{version}'
    pom = ET.parse(incoming / (stem + '.pom'))
    ns = '{http://maven.apache.org/POM/4.0.0}'
    for key, expected in [('groupId', 'com.arthenica'), ('artifactId', 'ffmpeg-kit-min'),
                          ('version', version), ('packaging', 'aar')]:
        if pom.findtext(ns + key) != expected:
            raise ValueError(f'Unexpected POM {key}')
    aar = incoming / (stem + '.aar')
    module = json.loads((incoming / (stem + '.module')).read_text())
    component = module['component']
    if (module['formatVersion'] != '1.1' or
            [component.get(key) for key in ['group', 'module', 'version']] !=
            ['com.arthenica', 'ffmpeg-kit-min', version]):
        raise ValueError('Unexpected Gradle module coordinates')
    file_entry = {'name': aar.name, 'url': aar.name, 'size': aar.stat().st_size}
    file_entry.update({key: hashlib.new(key, aar.read_bytes()).hexdigest()
                       for key in ['md5', 'sha1', 'sha256', 'sha512']})
    variants = module['variants']
    if not variants or any(variant.get('files') != [file_entry] for variant in variants):
        raise ValueError('Gradle variants must reference the verified local AAR')
    with zipfile.ZipFile(aar) as archive, tempfile.TemporaryDirectory() as temporary:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError('Corrupt or duplicate AAR entries')
        if not {'AndroidManifest.xml', 'classes.jar'} <= set(names):
            raise ValueError('AAR is missing its manifest or classes')
        for name in names:
            parts = name.split('/')
            if len(parts) == 3 and parts[:2] in [['jni', 'arm64-v8a'], ['jni', 'x86_64']] and parts[2].endswith('.so'):
                target = Path(temporary) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        verify_alignment(temporary)


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
    verify_candidate_contents(incoming, version)
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
    elif len(sys.argv) == 4 and sys.argv[1] == 'candidate':
        verify_candidate_contents(Path(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == 'metadata':
        update_metadata(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == 'stage':
        stage_artifacts(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit('Usage: maven-artifacts.py verify EXTRACTED_AAR | candidate INCOMING VERSION | metadata ARTIFACT_DIRECTORY VERSION | stage INCOMING ARTIFACT_DIRECTORY VERSION')
