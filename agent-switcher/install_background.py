#!/usr/bin/env python3
"""Install a per-session launchd interval job (no KeepAlive daemon)."""
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

socket=os.environ.get('HERDR_SOCKET_PATH') or str(Path.home()/'.config/herdr/herdr.sock')
label='com.jiajun.herdr-agent-switcher.'+hashlib.sha256(socket.encode()).hexdigest()[:8]
path=Path.home()/'Library/LaunchAgents'/f'{label}.plist'
if path.exists():
    subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}',str(path)],capture_output=True)
path.parent.mkdir(parents=True,exist_ok=True)
job={'Label':label,'ProgramArguments':[shutil.which('herdr'),'plugin','action','invoke','thejiajun.agent-switcher.refresh'],
     'StartInterval':120,'RunAtLoad':True,'ProcessType':'Background',
     'EnvironmentVariables':{'PATH':os.environ['PATH'],'HERDR_SOCKET_PATH':socket,'HERDR_ENV':'1'},
     'WorkingDirectory':str(Path(__file__).resolve().parent)}
with path.open('wb') as f: plistlib.dump(job,f)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True)
print(label)
