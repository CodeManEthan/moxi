"""Windows calls the core makes through ctypes: the user's SID, private directories and Job
Objects.

Nothing here runs on another system; every function imports ctypes and binds the Win32
calls it needs when first called, so importing this module costs nothing anywhere.

- `private_dir(path)` makes a directory whose access list grants only the current user,
  and checks it by reading the list back.
- `make_private_dir(path)` creates one such directory and leaves an existing one alone;
  `private_file(path)` creates a new file whose access list grants only the current user.
- `user_sid()` is the current user's SID as text, `S-1-5-21-...`.
- `pipe_client_pid(handle)` is the pid of the process at the client end of a named pipe,
  and `pipe_server_pid(handle)` the pid of the process at its server end.
- `Job` is a Job Object: the processes put in it can be ended together, and, made with
  `kill_on_close=True`, they end when the job's last handle closes, which happens when
  the process holding it dies.
"""

from __future__ import annotations

import os
from pathlib import Path

# Win32 constants.
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183
_GENERIC_WRITE = 0x40000000
_CREATE_NEW = 1
_FILE_ATTRIBUTE_NORMAL = 0x80
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x1
_DACL_SECURITY_INFORMATION = 0x4
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_DACL_PROTECTED = 0x1000
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_TOKEN_OWNER = 4
_ACCESS_ALLOWED_ACE_TYPE = 0
_ACCESS_DENIED_ACE_TYPE = 1
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800

_api = None


def _bind():
    """Load the Win32 calls once, with their argument and result types."""
    global _api
    if _api is not None:
        return _api
    import ctypes
    from ctypes import wintypes as w

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    P = ctypes.c_void_p
    PP = ctypes.POINTER(ctypes.c_void_p)

    def bind(dll, name, restype, *argtypes):
        fn = getattr(dll, name)
        fn.restype = restype
        fn.argtypes = list(argtypes)
        return fn

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", P),
                    ("bInheritHandle", w.BOOL)]

    class ACL(ctypes.Structure):
        _fields_ = [("AclRevision", w.BYTE), ("Sbz1", w.BYTE), ("AclSize", w.WORD),
                    ("AceCount", w.WORD), ("Sbz2", w.WORD)]

    class ACE_HEADER(ctypes.Structure):
        _fields_ = [("AceType", w.BYTE), ("AceFlags", w.BYTE), ("AceSize", w.WORD)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", w.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", w.LARGE_INTEGER),
                    ("LimitFlags", w.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", w.DWORD),
                    ("SchedulingClass", w.DWORD)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class Api:
        pass

    api = Api()
    api.ctypes = ctypes
    api.w = w
    api.SECURITY_ATTRIBUTES = SECURITY_ATTRIBUTES
    api.ACL = ACL
    api.ACE_HEADER = ACE_HEADER
    api.JOBOBJECT_EXTENDED_LIMIT_INFORMATION = JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    api.GetCurrentProcess = bind(kernel32, "GetCurrentProcess", w.HANDLE)
    api.CloseHandle = bind(kernel32, "CloseHandle", w.BOOL, w.HANDLE)
    api.LocalFree = bind(kernel32, "LocalFree", P, P)
    api.CreateDirectoryW = bind(kernel32, "CreateDirectoryW", w.BOOL, w.LPCWSTR,
                                ctypes.POINTER(SECURITY_ATTRIBUTES))
    api.CreateFileW = bind(kernel32, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
                           ctypes.POINTER(SECURITY_ATTRIBUTES), w.DWORD, w.DWORD, w.HANDLE)
    api.OpenProcess = bind(kernel32, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    api.CreateJobObjectW = bind(kernel32, "CreateJobObjectW", w.HANDLE, P, w.LPCWSTR)
    api.SetInformationJobObject = bind(kernel32, "SetInformationJobObject", w.BOOL,
                                       w.HANDLE, ctypes.c_int, P, w.DWORD)
    api.AssignProcessToJobObject = bind(kernel32, "AssignProcessToJobObject", w.BOOL,
                                        w.HANDLE, w.HANDLE)
    api.TerminateJobObject = bind(kernel32, "TerminateJobObject", w.BOOL, w.HANDLE,
                                  w.UINT)
    api.OpenProcessToken = bind(advapi32, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD,
                                ctypes.POINTER(w.HANDLE))
    api.GetTokenInformation = bind(advapi32, "GetTokenInformation", w.BOOL, w.HANDLE,
                                   ctypes.c_int, P, w.DWORD, ctypes.POINTER(w.DWORD))
    api.ConvertSidToStringSidW = bind(advapi32, "ConvertSidToStringSidW", w.BOOL, P,
                                      ctypes.POINTER(w.LPWSTR))
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW = bind(
        advapi32, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL,
        w.LPCWSTR, w.DWORD, PP, ctypes.POINTER(w.ULONG))
    api.GetSecurityDescriptorDacl = bind(advapi32, "GetSecurityDescriptorDacl", w.BOOL, P,
                                         ctypes.POINTER(w.BOOL), PP,
                                         ctypes.POINTER(w.BOOL))
    api.GetSecurityDescriptorControl = bind(advapi32, "GetSecurityDescriptorControl",
                                            w.BOOL, P, ctypes.POINTER(w.WORD),
                                            ctypes.POINTER(w.DWORD))
    api.GetNamedSecurityInfoW = bind(advapi32, "GetNamedSecurityInfoW", w.DWORD,
                                     w.LPCWSTR, ctypes.c_int, w.DWORD, PP, PP, PP, PP, PP)
    api.SetNamedSecurityInfoW = bind(advapi32, "SetNamedSecurityInfoW", w.DWORD,
                                     w.LPWSTR, ctypes.c_int, w.DWORD, P, P, P, P)
    api.GetAce = bind(advapi32, "GetAce", w.BOOL, P, w.DWORD, PP)
    api.EqualSid = bind(advapi32, "EqualSid", w.BOOL, P, P)
    api.GetNamedPipeClientProcessId = bind(kernel32, "GetNamedPipeClientProcessId", w.BOOL,
                                           w.HANDLE, ctypes.POINTER(w.ULONG))
    api.GetNamedPipeServerProcessId = bind(kernel32, "GetNamedPipeServerProcessId", w.BOOL,
                                           w.HANDLE, ctypes.POINTER(w.ULONG))
    _api = api
    return api


def _error(what: str, code: int | None = None) -> OSError:
    import ctypes

    code = ctypes.get_last_error() if code is None else code
    return OSError(0, f"{what} failed: {ctypes.FormatError(code)}", None, code)


# -- the current user ---------------------------------------------------------------------

class _Sid:
    """A SID read from this process's token, kept in a buffer this object owns."""

    def __init__(self, buffer, pointer: int):
        self._buffer = buffer
        self.pointer = pointer

    def text(self) -> str:
        return _sid_text(self.pointer)


def _token_sid(kind: int) -> _Sid:
    """The token's user SID (`_TOKEN_USER`) or default owner SID (`_TOKEN_OWNER`)."""
    api = _bind()
    ctypes, w = api.ctypes, api.w
    token = w.HANDLE()
    if not api.OpenProcessToken(api.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _error("OpenProcessToken")
    try:
        size = w.DWORD()
        api.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not api.GetTokenInformation(token, kind, buffer, size, ctypes.byref(size)):
            raise _error("GetTokenInformation")
    finally:
        api.CloseHandle(token)
    # TOKEN_USER and TOKEN_OWNER both start with a pointer to the SID, inside the buffer.
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    return _Sid(buffer, pointer)


def user_sid() -> str:
    """The current user's SID as text, read from this process's token."""
    return _token_sid(_TOKEN_USER).text()


def _sid_text(pointer: int) -> str:
    api = _bind()
    text = api.w.LPWSTR()
    if not api.ConvertSidToStringSidW(pointer, api.ctypes.byref(text)):
        raise _error("ConvertSidToStringSidW")
    try:
        return text.value
    finally:
        api.LocalFree(api.ctypes.cast(text, api.ctypes.c_void_p))


# -- named pipes --------------------------------------------------------------------------

def pipe_client_pid(handle: int) -> int:
    """The pid of the process at the client end of the server end `handle` of a named
    pipe, as the system records it when the client connects. Raises `OSError` when it
    can't be read."""
    api = _bind()
    pid = api.w.ULONG()
    if not api.GetNamedPipeClientProcessId(handle, api.ctypes.byref(pid)):
        raise _error("GetNamedPipeClientProcessId")
    return pid.value


def pipe_server_pid(handle: int) -> int:
    """The pid of the process at the server end of the client end `handle` of a named
    pipe: the process that created the pipe instance it opened. Raises `OSError` when it
    can't be read."""
    api = _bind()
    pid = api.w.ULONG()
    if not api.GetNamedPipeServerProcessId(handle, api.ctypes.byref(pid)):
        raise _error("GetNamedPipeServerProcessId")
    return pid.value


# -- private directories ------------------------------------------------------------------

def private_dir(path: str | os.PathLike) -> None:
    """Make directory `path` (and its parents) so that only the current user can use it.

    The directory is created with an owner of this user and a protected access list
    (nothing inherited from its parent) with one entry: full control for this user,
    passed on to everything made inside it. An existing directory owned by this user (or
    by its token's default owner, the Administrators group for an elevated process) whose
    list grants anyone else is given that list. One owned by anyone else raises
    `PermissionError`. Either way the list is then read back, and `PermissionError` is
    raised unless it grants only this user. Each missing parent is created with the same
    list; a parent that exists is left as it is.
    """
    from .files import _make_dirs

    path = Path(path)
    _make_dirs(path.parent)
    user = _token_sid(_TOKEN_USER)
    sid = user.text()
    descriptor = _descriptor(f"O:{sid}D:P(A;OICI;FA;;;{sid})")
    try:
        if not _create_dir(path, descriptor):
            if not path.is_dir():
                raise NotADirectoryError(f"{path} is not a directory")
            if not _only_user(path, user):
                _take(path, user, descriptor)
    finally:
        _bind().LocalFree(descriptor)
    if not _only_user(path, user):
        raise PermissionError(f"{path} grants access to others than {sid}, and its access "
                              "list could not be set")


def make_private_dir(path: str | os.PathLike) -> bool:
    """Create directory `path`, whose parent must exist, with `private_dir`'s access list,
    and return True. When something is already at `path`, return False and change
    nothing: an existing directory keeps its owner and its list."""
    user = _token_sid(_TOKEN_USER)
    sid = user.text()
    descriptor = _descriptor(f"O:{sid}D:P(A;OICI;FA;;;{sid})")
    try:
        return _create_dir(Path(path), descriptor)
    finally:
        _bind().LocalFree(descriptor)


def private_file(path: str | os.PathLike) -> None:
    """Create the empty file `path`, which must not exist, owned by this user and with a
    protected access list that grants only this user. Raises `FileExistsError` when
    something is already at `path`; the directory it is in is not changed."""
    import errno

    api = _bind()
    ctypes = api.ctypes
    user = _token_sid(_TOKEN_USER)
    sid = user.text()
    descriptor = _descriptor(f"O:{sid}D:P(A;;FA;;;{sid})")
    try:
        attributes = api.SECURITY_ATTRIBUTES(ctypes.sizeof(api.SECURITY_ATTRIBUTES),
                                             descriptor, False)
        handle = api.CreateFileW(str(path), _GENERIC_WRITE, 0, ctypes.byref(attributes),
                                 _CREATE_NEW, _FILE_ATTRIBUTE_NORMAL, None)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            code = ctypes.get_last_error()
            if code in (_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS):
                raise FileExistsError(errno.EEXIST, "the file exists", str(path))
            raise _error(f"creating {path}", code)
        api.CloseHandle(handle)
    finally:
        api.LocalFree(descriptor)


def _descriptor(sddl: str):
    """A security descriptor made from `sddl`; the caller frees it with `LocalFree`."""
    api = _bind()
    ctypes = api.ctypes
    descriptor = ctypes.c_void_p()
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None):
        raise _error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    return descriptor


def _create_dir(path: Path, descriptor) -> bool:
    """Create directory `path` with `descriptor`; False when something is already there."""
    api = _bind()
    ctypes = api.ctypes
    attributes = api.SECURITY_ATTRIBUTES(ctypes.sizeof(api.SECURITY_ATTRIBUTES),
                                         descriptor, False)
    if api.CreateDirectoryW(str(path), ctypes.byref(attributes)):
        return True
    code = ctypes.get_last_error()
    if code != _ERROR_ALREADY_EXISTS:
        raise _error(f"creating {path}", code)
    return False


def _take(path: Path, user: _Sid, descriptor) -> None:
    """Give an existing directory of this user's the private list, or refuse."""
    api = _bind()
    ctypes = api.ctypes
    owner = _read(path, _OWNER_SECURITY_INFORMATION)
    try:
        ours = api.EqualSid(owner.owner, user.pointer)
        if not ours:
            default = _token_sid(_TOKEN_OWNER)
            ours = api.EqualSid(owner.owner, default.pointer)
        if not ours:
            raise PermissionError(f"{path} is owned by {_sid_text(owner.owner)}, not by "
                                  f"this user ({user.text()})")
    finally:
        owner.free()
    present, defaulted = api.w.BOOL(), api.w.BOOL()
    dacl = ctypes.c_void_p()
    if not api.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                                         ctypes.byref(dacl), ctypes.byref(defaulted)):
        raise _error("GetSecurityDescriptorDacl")
    code = api.SetNamedSecurityInfoW(
        str(path), _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, dacl, None)
    if code:
        raise _error(f"setting the access list of {path}", code)


class _Read:
    """What `GetNamedSecurityInfoW` returned; `free()` releases its descriptor."""

    def __init__(self, api, owner, dacl, descriptor):
        self._api = api
        self.owner = owner.value
        self.dacl = dacl.value
        self.descriptor = descriptor

    def free(self) -> None:
        if self.descriptor.value:
            self._api.LocalFree(self.descriptor)
            self.descriptor.value = None


def _read(path: Path, what: int) -> _Read:
    api = _bind()
    ctypes = api.ctypes
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    code = api.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, what, ctypes.byref(owner),
                                     None, ctypes.byref(dacl), None,
                                     ctypes.byref(descriptor))
    if code:
        raise _error(f"reading the security of {path}", code)
    return _Read(api, owner, dacl, descriptor)


def _only_user(path: Path, user: _Sid) -> bool:
    """Whether `path`'s access list is protected and allows no one but `user`."""
    api = _bind()
    ctypes = api.ctypes
    read = _read(path, _DACL_SECURITY_INFORMATION)
    try:
        if not read.dacl:
            return False  # no list at all: everyone has full access
        control, revision = api.w.WORD(), api.w.DWORD()
        if not api.GetSecurityDescriptorControl(read.descriptor, ctypes.byref(control),
                                                ctypes.byref(revision)):
            raise _error("GetSecurityDescriptorControl")
        if not control.value & _SE_DACL_PROTECTED:
            return False
        count = ctypes.cast(read.dacl, ctypes.POINTER(api.ACL)).contents.AceCount
        allowed = 0
        for index in range(count):
            ace = ctypes.c_void_p()
            if not api.GetAce(read.dacl, index, ctypes.byref(ace)):
                raise _error("GetAce")
            kind = ctypes.cast(ace, ctypes.POINTER(api.ACE_HEADER)).contents.AceType
            if kind == _ACCESS_DENIED_ACE_TYPE:
                continue
            if kind != _ACCESS_ALLOWED_ACE_TYPE:
                return False
            # ACCESS_ALLOWED_ACE: the header (4 bytes), the mask (4), then the SID.
            if not api.EqualSid(ace.value + 8, user.pointer):
                return False
            allowed += 1
        return allowed > 0
    finally:
        read.free()


# -- Job Objects --------------------------------------------------------------------------

class Job:
    """A Job Object. `assign(pid)` puts a process in it (the processes it starts later
    join too); `terminate()` ends every process in it; `close()` lets go of it.

    With `kill_on_close=True` every process in the job is ended when the job's last handle
    closes: on `close()`, when this object is garbage collected, or when this process dies.
    With `breakaway=True` a process in the job may start a child outside it
    (`CREATE_BREAKAWAY_FROM_JOB`, which `moxi.proc.spawn(detached=True)` asks for).
    """

    def __init__(self, *, kill_on_close: bool = False, breakaway: bool = False):
        api = _bind()
        handle = api.CreateJobObjectW(None, None)
        if not handle:
            raise _error("CreateJobObjectW")
        self._handle = handle
        flags = ((_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE if kill_on_close else 0)
                 | (_JOB_OBJECT_LIMIT_BREAKAWAY_OK if breakaway else 0))
        if flags:
            info = api.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = flags
            if not api.SetInformationJobObject(handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                               api.ctypes.byref(info),
                                               api.ctypes.sizeof(info)):
                error = _error("SetInformationJobObject")
                self.close()
                raise error

    def assign(self, pid: int) -> None:
        api = _bind()
        process = api.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            raise _error(f"opening process {pid}")
        try:
            if not api.AssignProcessToJobObject(self._handle, process):
                raise _error(f"putting process {pid} in a job")
        finally:
            api.CloseHandle(process)

    def terminate(self, exit_code: int = 1) -> None:
        if self._handle and not _bind().TerminateJobObject(self._handle, exit_code):
            raise _error("TerminateJobObject")

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            _bind().CloseHandle(handle)

    def __enter__(self) -> Job:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - nothing can be done about it at collection
            pass
