"""Restricted boot engine lifecycle, to be hosted by the installed non-root unit.

The supervisor/service adapter supplies validated root-owned executable/config
paths. This controller accepts no client commands, desktop sockets or settings.
"""
import re
from .engine import RetrySchedule
from monitorize.platform.sunshine_ports import SUNSHINE_BASE_PORT


class BootHost:
    def __init__(self,engine,paths,executable,config,assets,*,prepare=None):
        self.engine=engine;self.paths=paths;self.executable=executable
        self.config=config;self.assets=assets;self.retry=RetrySchedule()
        self.closed=False;self.output=None;self.state='waiting_for_greeter'
        self.started=False;self.prepare=prepare;self.control_environment=None
        self.startup_error='';self.startup_deadline=None

    def step(self,phase,output,now):
        if self.closed:return False
        if phase not in ('greeter','waiting'):
            self.close();self.state='stopped_for_login';return False
        if output is not None and not re.fullmatch(r'Virtual-[0-9]+',output):
            raise ValueError('Invalid exact boot output.')
        if self.control_environment is not None and output is None:output='Virtual-0'
        if self.output is not None and output!=self.output:
            self.engine.stop();self.retry.failed(now);self.output=None
            self.state='output_changed';return True
        code=self.engine.reap()
        if code is not None:
            self.retry.failed(now);self.state='retry_wait';self.output=None
            self.startup_error=f'Boot Sunshine exited (status {code}). Inspect the boot engine journal and boot.log.'
            self.startup_deadline=None
        if phase!='greeter' or output is None:
            if self.engine.running:self.engine.stop()
            self.state='waiting_for_greeter';return True
        if self.engine.running:
            # Listening/capture readiness is a separate validated health signal.
            self.state='engine_running';return True
        if self.retry.deadline is not None and not self.retry.due(now):
            self.state='retry_wait';return True
        argv=[str(self.executable),str(self.config),'capture=kms','output_name='+output,
              'stream_audio=disabled','controller=disabled','upnp=disabled',
              'origin_web_ui_allowed=pc',f'port={SUNSHINE_BASE_PORT}']
        argv.extend(key+'='+value for key,value in self.paths.config_overrides().items())
        environment=dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin',LANG='C.UTF-8',
            HOME=str(self.paths.lock.parent),XDG_CONFIG_HOME=str(self.paths.lock.parent),
            XDG_RUNTIME_DIR=str(self.paths.lock.parent/'runtime'),XDG_SEAT='seat0',
            SUNSHINE_ASSETS_DIR=str(self.assets),MONITORIZE_REQUIRE_EXACT_OUTPUT='1',
            MONITORIZE_CAPTURE_OUTPUT=output)
        if self.control_environment is not None:
            environment.update(self.control_environment)
            environment.pop('MONITORIZE_CAPTURE_OUTPUT',None)
        try:
            if self.prepare is None:self.engine.start(argv,environment)
            else:self.engine.start(argv,environment,prepare=self.prepare)
        except (OSError,ValueError,RuntimeError) as error:
            self.startup_error='Boot Sunshine startup failed: '+type(error).__name__+'. Inspect the boot engine journal.'
            print(self.startup_error,flush=True)
            self.retry.failed(now);self.state='start_failed';return True
        self.output=output;self.started=True;self.state='engine_running'
        self.startup_deadline=now+45
        # Retry attempts reset only after an explicit, sustained health result,
        # not merely Popen success; repeated startup exits therefore back off.
        return True

    def healthy(self):
        if self.engine.running:self.retry.healthy()

    def observe_listener(self,listening,now):
        if listening:
            self.startup_error='';self.startup_deadline=None
        elif self.engine.running and self.startup_deadline is not None and now>=self.startup_deadline:
            self.engine.stop();self.retry.failed(now);self.output=None
            self.startup_error='Boot Sunshine did not start listening within 45 seconds. Inspect the boot engine journal and boot.log.'
            self.startup_deadline=None;self.state='retry_wait'
            print(self.startup_error,flush=True)

    def close(self):
        self.retry.cancel();self.engine.stop();self.closed=True;self.state='stopped'
