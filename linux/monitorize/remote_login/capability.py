"""Validate the installed fork's exact-output contract without executing it."""
import mmap
import hashlib
from functools import lru_cache
from pathlib import Path

MARKERS=(b'MONITORIZE_CONTROL_PROTOCOL=1',b'MONITORIZE_STRICT_KMS_OUTPUT_MISSING')
ENGINE=Path('/usr/libexec/monitorize/sunshine')


@lru_cache(maxsize=8)
def _engine_digest(path,metadata):
    # File identity/timestamps invalidate the cache after rebuild, install or rollback.
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').digest()


def matching_engines(source,installed=ENGINE):
    """Compare bytes without executing either binary or depending on debug symbols."""
    def digest(path):
        path=Path(path);info=path.stat()
        return _engine_digest(path,(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns))
    try:return digest(source)==digest(installed)
    except OSError:return False


def source_engine_update_error(*,repository=None,installed=ENGINE):
    """Only a source checkout with its own installed engine needs synchronization."""
    repository=Path(repository) if repository is not None else Path(__file__).resolve().parents[3]
    if not (repository/'linux/scripts/install.sh').is_file():return ''
    from monitorize.platform.sunshine_service import get_sunshine_candidates
    candidates=get_sunshine_candidates()
    if not candidates:return ''
    source=Path(candidates[0][0])
    if not source.is_relative_to(repository):return ''
    if matching_engines(source,installed):return ''
    return ('The source desktop engine ('+str(source)+') and installed remote-login engine differ. '
            'For source-managed service files, open Settings and choose Repair remote login to install matching files with administrator authorization. '
            'Package-owned service files must match the running app version and remain managed by the package manager.')


def exact_kms_engine(path):
    try:
        with Path(path).open('rb') as source:
            if source.read(4)!=b'\x7fELF':return False
            with mmap.mmap(source.fileno(),0,access=mmap.ACCESS_READ) as binary:
                return all(binary.find(marker)>=0 for marker in MARKERS)
    except (OSError,ValueError):return False
