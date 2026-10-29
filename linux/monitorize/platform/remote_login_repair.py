"""Graphical service repair for source deployments; preserve native package ownership."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile

from .system_setup import parse_system_setup_result
from .polkit_agent import run_authorized

HELPER = Path('/usr/libexec/monitorize/remote-login/repair.py')
ENGINE = Path('/usr/libexec/monitorize/sunshine')


def owning_package_manager(path):
    """Check every database; build hosts can have several package tools installed."""
    commands = (('pacman', ['/usr/bin/pacman', '-Qoq', '--']),
                ('deb', ['/usr/bin/dpkg-query', '-S', '--']),
                ('rpm', ['/usr/bin/rpm', '-qf', '--']))
    for manager, command in commands:
        if not Path(command[0]).is_file():
            continue
        result = run_authorized([*command, str(path)], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode not in (0, 1):
            raise ValueError('Could not verify installed file ownership. Repair through your package manager.')
        if result.returncode == 0:
            return manager
    return ''


def repair_options(*, repository=None):
    repository = Path(repository) if repository is not None else Path(__file__).resolve().parents[3]
    source = (repository / 'scripts/stage-remote-login.py').is_file()
    try:
        owned = ''
        for path in (ENGINE, HELPER, Path('/usr/libexec/monitorize/monitorize-system-setup'), Path(__file__)):
            if path.exists():
                owned = owning_package_manager(path)
                if owned:
                    break
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'repair_kind': '', 'repair_message': str(error)}
    kind = 'source' if source and not owned else ''
    helper = HELPER if HELPER.is_file() else repository / 'packaging/remote-login/repair.py'
    pkexec = shutil.which('pkexec')
    available = bool(kind and helper.is_file() and pkexec)
    from monitorize.remote_login.capability import source_engine_update_error
    needed = bool(source and source_engine_update_error(repository=repository))
    message = 'Repair service files with administrator authorization. Settings and pairing are retained.'
    if owned or not source:
        message = 'Remote-login files are managed by the native Monitorize package.'
    elif not pkexec:
        message = 'Install Polkit (pkexec) and start a desktop authentication agent to enable graphical repair.'
    elif not helper.is_file():
        message = 'The source remote-login repair helper is missing.'
    return {'repair_kind': kind if available else '', 'repair_source': source,
            'repair_needed': needed, 'repair_message': message}


def repair_remote_login(*, repository=None):
    repository = Path(repository) if repository is not None else Path(__file__).resolve().parents[3]
    options = repair_options(repository=repository)
    kind = options['repair_kind']
    if not kind:
        return {'success': False, 'message': options['repair_message']}
    pkexec = shutil.which('pkexec')
    helper = HELPER if HELPER.is_file() else repository / 'packaging/remote-login/repair.py'
    # New installs use the dedicated installed policy. An older source install
    # bootstraps via the ordinary Polkit Python prompt, just as its explicit
    # manual installer did; no terminal/sudo commands or printed output parsing.
    command = [pkexec, str(helper)] if helper == HELPER else [pkexec, '/usr/bin/python3', '-I', str(helper)]
    try:
        with tempfile.TemporaryDirectory(prefix='monitorize-service-stage-') as temporary:
            from monitorize.platform.sunshine_service import _sunshine_bundles, get_sunshine_candidates
            candidates = get_sunshine_candidates()
            if not candidates:
                raise ValueError('Build the Monitorize Sunshine engine before repairing source service files.')
            binary = Path(candidates[0][0])
            assets = dict(_sunshine_bundles()).get(str(binary))
            if not binary.is_relative_to(repository) or not assets:
                raise ValueError('Select the engine from this Monitorize source installation before repairing services.')
            spec = importlib.util.spec_from_file_location('monitorize_stage', repository / 'scripts/stage-remote-login.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            staged = module.prepare(Path(temporary) / 'stage', binary, Path(assets), announce=False)
            input_path = staged
            result = run_authorized([*command, '--kind', 'source', '--input', str(input_path)],
                                    capture_output=True, text=True, check=False)
            return parse_system_setup_result(result.returncode, result.stdout, result.stderr)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return {'success': False, 'message': 'Remote-login repair failed: ' + str(error)}
