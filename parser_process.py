"""Killable document-processing boundary. Not a filesystem/network sandbox."""
from dataclasses import dataclass
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

_GATE = threading.BoundedSemaphore(1)
_TEMP_ROOT = None
_WORKER = Path(__file__).resolve()


class ProcessingUnavailable(Exception):
    """Safe to map to a generic HTTP error; never include child exceptions."""


@dataclass(frozen=True)
class Limits:
    seconds: float = 20
    memory_mb: int = 1024
    cpu_seconds: int = 10
    output_bytes: int = 2 * 1024 * 1024

    def __post_init__(self):
        if (type(self.seconds) not in (int,float) or not math.isfinite(self.seconds)
                or not 0 < self.seconds <= 120 or type(self.memory_mb) is not int
                or not 256 <= self.memory_mb <= 4096 or type(self.cpu_seconds) is not int
                or not 1 <= self.cpu_seconds <= 120 or type(self.output_bytes) is not int
                or not 1024 <= self.output_bytes <= 2*1024*1024):
            raise ValueError('Invalid processing limits')


class _WindowsJob:
    """A job is configured before releasing the trusted bootstrap barrier."""
    def __init__(self, limits):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.api = c, c.WinDLL('kernel32',use_last_error=True)
        class Basic(c.Structure):
            _fields_=[('process_time',c.c_longlong),('job_time',c.c_longlong),('flags',w.DWORD),
                      ('min_working',c.c_size_t),('max_working',c.c_size_t),('active',w.DWORD),
                      ('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(c.Structure):
            _fields_=[(name,c.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(c.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',c.c_size_t),('job_memory',c.c_size_t),
                      ('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
        self.api.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR]
        self.api.CreateJobObjectW.restype=w.HANDLE
        self.api.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
        self.api.SetInformationJobObject.restype=w.BOOL
        self.api.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
        self.api.OpenProcess.restype=w.HANDLE
        self.api.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.api.AssignProcessToJobObject.restype=w.BOOL
        self.api.CloseHandle.argtypes=[w.HANDLE]
        self.api.CloseHandle.restype=w.BOOL
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle:
            raise ProcessingUnavailable()
        info=Extended()
        info.basic.flags=0x2000 | 0x100 | 0x8 | 0x2 # kill-on-close, memory, one process, CPU
        info.basic.active=1
        info.basic.process_time=limits.cpu_seconds*10_000_000
        info.process_memory=limits.memory_mb*1024*1024
        if not self.api.SetInformationJobObject(self.handle,9,c.byref(info),c.sizeof(info)):
            self.close()
            raise ProcessingUnavailable()

    def assign(self,pid):
        handle=self.api.OpenProcess(0x101,False,pid) # SET_QUOTA | TERMINATE
        if not handle:
            raise ProcessingUnavailable()
        try:
            if not self.api.AssignProcessToJobObject(self.handle,handle):
                raise ProcessingUnavailable()
        finally:
            self.api.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle=None


def _validate_result(value):
    if not isinstance(value,dict) or set(value) != {'status','issues'} or value['status'] != 'ok':
        raise ProcessingUnavailable()
    issues=value['issues']
    keys={'severity','code','file','location','message','recommendation'}
    if not isinstance(issues,list) or len(issues)>10000:
        raise ProcessingUnavailable()
    for issue in issues:
        if (not isinstance(issue,dict) or set(issue)!=keys
                or any(not isinstance(v,str) or len(v)>16000 for v in issue.values())
                or issue['severity'] not in ('info','warning','error')
                or re.fullmatch(r'KD-(?:[A-Z]+-)?\d{3}',issue['code']) is None):
            raise ProcessingUnavailable()
    return issues


def _kill(process):
    if process.poll() is None:
        if os.name == 'posix':
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
    process.wait(timeout=5)


def run_check(files, config, limits=None, *, capacity=None, cancelled=None):
    limits=limits or Limits()
    if not isinstance(limits,Limits):
        raise ValueError('Explicit Limits required')
    if cancelled is not None and not isinstance(cancelled,threading.Event):
        raise ValueError('Explicit cancellation Event required')
    if cancelled is not None and cancelled.is_set():
        raise ProcessingUnavailable()
    if (not isinstance(files,list) or not 1 <= len(files) <= 100
            or any(not isinstance(name,str) or not name or len(name)>1000
                   or not isinstance(content,bytes) or len(content)>20*1024*1024 for name,content in files)
            or sum(len(content) for _,content in files)>100*1024*1024):
        raise ValueError('Invalid bounded input')
    if not _GATE.acquire(blocking=False):
        raise ProcessingUnavailable()
    try:
        with tempfile.TemporaryDirectory(prefix='kd-parser-',dir=_TEMP_ROOT) as directory:
            root=Path(directory)
            manifest={'config':config,'limits':vars(limits),'files':[],
                      'capacity':str(capacity) if capacity is not None else None}
            for index,(name,content) in enumerate(files):
                filename=f'input-{index}.bin'
                (root/filename).write_bytes(content)
                manifest['files'].append({'name':name,'path':filename})
            encoded=json.dumps(manifest,ensure_ascii=False,allow_nan=False).encode('utf-8')
            if len(encoded)>256*1024:
                raise ValueError('Manifest too large')
            (root/'manifest.json').write_bytes(encoded)
            env={key:os.environ[key] for key in ('SystemRoot','WINDIR','PATH') if key in os.environ}
            env.update(TEMP=directory,TMP=directory,HOME=directory,USERPROFILE=directory,
                       OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
            job,process=None,None
            try:
                if os.name == 'nt':
                    job=_WindowsJob(limits)
                process=subprocess.Popen([sys.executable,'-I',str(_WORKER),'--worker',directory],
                    cwd=directory,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,start_new_session=os.name=='posix',
                    creationflags=0x08000000 if os.name=='nt' else 0)
                if job:
                    job.assign(process.pid)
                (root/'go').touch(exist_ok=False)
                deadline=time.monotonic()+limits.seconds
                while True:
                    if cancelled is not None and cancelled.is_set():
                        _kill(process)
                        raise ProcessingUnavailable()
                    remaining=deadline-time.monotonic()
                    if remaining <= 0:
                        _kill(process)
                        raise ProcessingUnavailable()
                    try:
                        code=process.wait(timeout=min(remaining,.05) if cancelled is not None else remaining)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if code != 0:
                    raise ProcessingUnavailable()
                result=root/'result.json'
                if not stat.S_ISREG(result.lstat().st_mode) or result.stat().st_size>limits.output_bytes:
                    raise ProcessingUnavailable()
                with result.open('rb') as stream:
                    raw=stream.read(limits.output_bytes+1)
                if len(raw)>limits.output_bytes:
                    raise ProcessingUnavailable()
                issues=_validate_result(json.loads(raw))
                if cancelled is not None and cancelled.is_set():
                    raise ProcessingUnavailable()
                return issues
            except (OSError,ValueError,RecursionError,subprocess.SubprocessError):
                raise ProcessingUnavailable() from None
            finally:
                if process is not None and process.poll() is None:
                    _kill(process)
                if job is not None:
                    job.close()
    finally:
        _GATE.release()


def _posix_limits(limits):
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_AS,(limits.memory_mb*1024*1024,)*2)
    resource.setrlimit(resource.RLIMIT_CPU,(limits.cpu_seconds,)*2)
    resource.setrlimit(resource.RLIMIT_FSIZE,(limits.output_bytes,)*2)
    if hasattr(resource,'RLIMIT_NPROC'):
        resource.setrlimit(resource.RLIMIT_NPROC,(1,1))


def _worker(directory):
    root=Path(directory)
    deadline=time.monotonic()+5
    while not (root/'go').exists():
        if time.monotonic()>deadline:
            return 1
        time.sleep(.01)
    manifest=json.loads((root/'manifest.json').read_bytes())
    limits=Limits(**manifest['limits'])
    if os.name == 'posix':
        _posix_limits(limits)
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    # The actual parser owns the lock. A dying HTTP parent must not free its
    # capacity while an orphan parser is still running. No lease expiry races.
    from parser_capacity import claim
    with claim(manifest['capacity']) if manifest.get('capacity') is not None else nullcontext():
        return _parse(root, manifest, limits)


def _parse(root, manifest, limits):
    from secure_backend import check_documents
    files=[(row['name'],(root/row['path']).read_bytes()) for row in manifest['files']]
    issues=check_documents(files,manifest['config'])
    output=json.dumps({'status':'ok','issues':issues},ensure_ascii=False,allow_nan=False).encode('utf-8')
    if len(output)>limits.output_bytes:
        return 1
    with (root/'result.json').open('xb') as stream:
        stream.write(output)
    return 0


if __name__ == '__main__':
    try:
        result=_worker(sys.argv[2]) if len(sys.argv)==3 and sys.argv[1]=='--worker' else 1
    except BaseException:
        result=1
    sys.exit(result)
