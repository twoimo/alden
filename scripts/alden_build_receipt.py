"""Reuse only an unchanged, previously verified canonical Alden build.

Never delete targets/backups or rewrite a reused bundle's source provenance.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

DEPENDENCY_CACHE_NAMES = {'.cache', '.vite', '.vite-temp', '__pycache__'}

def tree_digest(folder, *, dependency=False):
    """Hash installed dependency bytes, including resolved npm executable links."""
    if not folder.is_dir():
        raise RuntimeError('build_dependency_missing')
    values = {}
    for base, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in DEPENDENCY_CACHE_NAMES)
        for name in sorted(files):
            path = Path(base) / name
            relative = path.relative_to(folder).as_posix()
            if path.is_symlink():
                target = path.resolve(strict=True)
                if not dependency or not target.is_relative_to(folder.resolve()):
                    raise RuntimeError('build_input_symlink_unsafe')
                values[relative] = [os.readlink(path), sha(target)]
            else:
                values[relative] = [sha(path), path.stat().st_mode & 0o777]
        if any((Path(base)/d).is_symlink() for d in dirs):
            raise RuntimeError('build_input_directory_symlink_unsafe')
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

def dependencies(root):
    values = {'npm': tree_digest(root/'desktop/node_modules', dependency=True)}
    seen = set()
    # rustc's completed dep-info identifies the source crates actually compiled
    # on this host. Cargo metadata can require uncached foreign-target crates;
    # a cache check must never download them or expand Cargo's registry.
    for directory in ['target/release/deps', 'desktop/src-tauri/target/release/deps']:
        files = list((root/directory).glob('*.d'))
        if not files:
            raise RuntimeError('build_dependency_evidence_missing')
        for info in files:
            lines = info.read_text().splitlines()
            if not lines or ': ' not in lines[0]:
                raise RuntimeError('build_dependency_evidence_invalid')
            line = lines[0]
            for name in shlex.split(line.partition(': ')[2]):
                path = Path(name)
                if not path.is_absolute() or path.is_relative_to(root):
                    continue
                if not path.is_file():
                    raise RuntimeError('build_dependency_missing')
                folder = next((p for p in path.parents if (p/'Cargo.toml').is_file()), None)
                if folder is None:
                    values['external:'+str(path)] = sha(path)
                elif folder not in seen:
                    seen.add(folder)
                    values['rust:'+str(folder)] = tree_digest(folder, dependency=True)
    return values

def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def inputs(root):
    names=set()
    for directory in ['src','scripts','schemas','voice/native','desktop/src','desktop/src-tauri/src',
                      'desktop/src-tauri/icons','desktop/src-tauri/capabilities','.cargo','desktop/.cargo','desktop/src-tauri/.cargo']:
        folder=root/directory
        if not folder.is_dir():continue
        for path in folder.rglob('*'):
            if path.is_symlink():raise RuntimeError('build_input_symlink_unsafe')
            if path.name != 'libalden_audio.dylib' and path.is_file() and '__pycache__' not in path.parts and not path.is_symlink():names.add(path.relative_to(root).as_posix())
    for name in ['Cargo.toml','Cargo.lock','build.rs','desktop/package.json','desktop/package-lock.json','desktop/index.html',
                 'desktop/vite.config.ts','desktop/tsconfig.json','desktop/src-tauri/Cargo.toml','desktop/src-tauri/Cargo.lock',
                 'desktop/src-tauri/build.rs','desktop/src-tauri/tauri.conf.json','desktop/src-tauri/Info.plist',
                 'rust-toolchain','rust-toolchain.toml','desktop/rust-toolchain.toml','desktop/src-tauri/rust-toolchain.toml']:
        if (root/name).is_file():names.add(name)
    files={name:sha(root/name) for name in sorted(names)}
    # A clean/fixture checkout has no verified dependency evidence. Refuse reuse
    # before invoking tool shims (rustup may otherwise download a toolchain).
    dependency_input=dependencies(root)
    environment={key:hashlib.sha256(os.environ.get(key,'').encode()).hexdigest() for key in ['OPENKAKAO_SIGN_IDENTITY','APPLE_SIGNING_IDENTITY','OPENKAKAO_TAURI_SOURCE_CHECK','TAURI_CONFIG','CARGO_TARGET_DIR','RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS','MACOSX_DEPLOYMENT_TARGET','SDKROOT','CC','CXX']}
    tools={}
    for name in ['rustc','cargo','node','npm']:
        result=subprocess.run([name,'--version'],capture_output=True,text=True,check=True)
        tools[name]=result.stdout.strip()
    if os.uname().sysname == 'Darwin':
        for name, command in [('swift', ['xcrun','swiftc','--version']), ('macos_sdk', ['xcrun','--sdk','macosx','--show-sdk-version'])]:
            tools[name] = subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
    value={'files':files,'environment':environment,'tools':tools,'dependencies':dependency_input}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest(),value

def output_manifest(app):
    paths = sorted(app.rglob('*'))
    if any(path.is_symlink() for path in paths):
        raise RuntimeError('build_output_symlink_unsafe')
    return {path.relative_to(app).as_posix():[sha(path),path.stat().st_mode & 0o777] for path in paths if path.is_file()}

def verified(root, receipt):
    app=root/'desktop/src-tauri/target/release/bundle/macos/Alden.app'
    cli=root/'target/release/openkakao-cli'
    if not app.is_dir() or not cli.is_file() or app.is_symlink() or cli.is_symlink() or not os.access(cli,os.X_OK):return False
    key,_=inputs(root)
    if key!=receipt.get('input_digest') or sha(cli)!=receipt.get('cli_sha256'):return False
    if output_manifest(app)!=receipt.get('output_manifest'):return False
    return subprocess.run(['codesign','--verify','--deep','--strict',str(app)],capture_output=True).returncode==0

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['key','check','record','run'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--expected-key')
    args=parser.parse_args();root=args.root.resolve();path=root/'desktop/src-tauri/target/alden-build-receipt.json'
    if args.mode=='run':
        (root/'target').mkdir(exist_ok=True)
        fd=os.open(root/'target/alden-canonical-build.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise SystemExit('build-alden-desktop: canonical build already running')
            env=dict(os.environ,ALDEN_CANONICAL_BUILD_LOCKED='1')
            raise SystemExit(subprocess.run(['sh',str(root/'scripts/build-alden-desktop.sh')],env=env).returncode)
        finally:os.close(fd)
    if args.mode=='key':
        try:print(inputs(root)[0])
        except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError):print('')
        return
    if args.mode=='check':
        try:
            if path.is_symlink():raise SystemExit(1)
            receipt=json.loads(path.read_text())
            if not isinstance(receipt,dict) or not verified(root,receipt):raise SystemExit(1)
        except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError):raise SystemExit(1)
        print(root/'desktop/src-tauri/target/release/bundle/macos/Alden.app');return
    if not args.expected_key:return  # A clean build has no prior dep-info yet.
    key,evidence=inputs(root)
    if not args.expected_key or key!=args.expected_key:raise RuntimeError('build_inputs_changed_during_build')
    app=root/'desktop/src-tauri/target/release/bundle/macos/Alden.app';cli=root/'target/release/openkakao-cli'
    receipt={'input_digest':key,'inputs':evidence,'cli_sha256':sha(cli),'output_manifest':output_manifest(app)}
    if not verified(root,receipt):
        # Unsigned source-check builds remain valid build outcomes but cannot
        # become a reusable signed installation. Preserve their normal result.
        if not os.environ.get('OPENKAKAO_SIGN_IDENTITY'):return
        raise RuntimeError('canonical_build_verification_failed')
    fd,temporary=tempfile.mkstemp(prefix='alden-build-receipt.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as handle:json.dump(receipt,handle,indent=2);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)

if __name__=='__main__':main()
