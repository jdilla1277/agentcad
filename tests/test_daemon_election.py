"""Real process startup elections, always on private isolated endpoints."""
# collect-on-default-profile
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import pytest
from agentcad import daemon

pytestmark = pytest.mark.skipif(not daemon.daemon_supported(), reason='POSIX daemon')


def environment():
    env = {**os.environ, 'PYTHONPATH': str(Path(daemon.__file__).resolve().parents[1]), 'AGENTCAD_NO_LOG':'1'}
    env.pop('AGENTCAD_DAEMON', None)
    return env


def wait_for(predicate, timeout=10):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail('Timed out waiting for isolated test process')


@pytest.fixture
def endpoint():
    with tempfile.TemporaryDirectory(prefix='ac73-', dir='/tmp') as folder:
        root = Path(folder)
        yield root, str(root/'d.sock'), str(root/'d.pid')


def test_status_preserves_pid_between_publication_and_bind(endpoint,monkeypatch):
    root,sock,pid = endpoint
    script = '''
import os,sys,time
from pathlib import Path
from agentcad import daemon
sock,pid,marker,release=sys.argv[1:]
original=daemon._write_pid_metadata
def gated(*a,**kw):
    original(*a,**kw)
    Path(marker).touch()
    while not Path(release).exists(): time.sleep(.01)
daemon._write_pid_metadata=gated
daemon.DaemonServer(socket_path=sock,pid_path=pid).serve()
'''
    process = subprocess.Popen([sys.executable,'-c',script,sock,pid,str(root/'published'),str(root/'release')],env=environment())
    try:
        wait_for(lambda:(root/'published').exists())
        before=Path(pid).read_bytes()
        status=daemon.daemon_status(socket_path=sock,pid_path=pid)
        assert Path(pid).exists(),status
        assert Path(pid).read_bytes()==before
        assert status['starting'] is True and status['state_preserved'] is True
        stopped=daemon.stop_daemon(socket_path=sock,pid_path=pid)
        assert stopped['stopped'] is False and stopped['state_preserved'] is True
        waiting=daemon.start_daemon(socket_path=sock,pid_path=pid,timeout_s=0.05)
        assert waiting['started'] is False and waiting['state_preserved'] is True
        monkeypatch.delenv('AGENTCAD_DAEMON',raising=False)
        automatic=daemon.spawn_daemon_via_fork(socket_path=sock,pid_path=pid)
        assert automatic['spawned'] is False and automatic['reason']=='startup_in_progress'
        assert Path(pid).read_bytes()==before
        (root/'release').touch()
        wait_for(lambda:daemon.daemon_status(socket_path=sock,pid_path=pid).get('running'))
    finally:
        (root/'release').touch()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_second_direct_server_does_not_replace_live_endpoint(endpoint):
    root,sock,pid=endpoint
    script='from agentcad.daemon import DaemonServer; import sys; DaemonServer(socket_path=sys.argv[1],pid_path=sys.argv[2]).serve()'
    processes=[]
    try:
        processes.append(subprocess.Popen([sys.executable,'-c',script,sock,pid],env=environment()))
        wait_for(lambda:daemon.daemon_status(socket_path=sock,pid_path=pid).get('running'))
        before=Path(pid).read_bytes()
        processes.append(subprocess.Popen([sys.executable,'-c',script,sock,pid],env=environment()))
        try:
            processes[1].wait(timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail('Second daemon became another serving process')
        assert Path(pid).read_bytes()==before
        assert daemon.daemon_status(socket_path=sock,pid_path=pid)['pid']==processes[0].pid
    finally:
        for process in processes:
            if process.poll() is None: process.kill()
            process.wait(timeout=5)


def test_concurrent_explicit_starts_elect_one_reachable_worker(endpoint):
    root,sock,pid=endpoint
    script='import json,sys; from agentcad.daemon import start_daemon; print(json.dumps(start_daemon(socket_path=sys.argv[1],pid_path=sys.argv[2],timeout_s=20)))'
    callers=[]
    results=[]
    try:
        callers=[subprocess.Popen([sys.executable,'-c',script,sock,pid],env=environment(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(3)]
        for caller in callers:
            out,err=caller.communicate(timeout=30)
            assert caller.returncode==0,err
            results.append(json.loads(out))
        assert sum(r['started'] for r in results)==1,results
        assert len({r['pid'] for r in results})==1,results
        status=daemon.daemon_status(socket_path=sock,pid_path=pid)
        assert status['running'] is True and status['pid']==results[0]['pid']
        stopped=daemon.stop_daemon(socket_path=sock,pid_path=pid)
        assert stopped['stopped'] is True,stopped
    finally:
        for caller in callers:
            if caller.poll() is None: caller.kill()
            caller.wait(timeout=5)
        for worker in {r['pid'] for r in results if r.get('started') and r.get('pid')}:
            try:
                if os.getpgid(worker)==worker: os.kill(worker,signal.SIGKILL)
            except ProcessLookupError: pass


@pytest.mark.parametrize('modes',[['fork','fork','fork'],['fork','exec','exec']])
def test_concurrent_auto_and_mixed_starters_share_one_worker(endpoint,modes):
    root,sock,pid=endpoint
    script='''
import json,os,sys
from agentcad import daemon
mode,sock,pid,log=sys.argv[1:]
original=daemon._write_pid_metadata
def record(*args,**kwargs):
    original(*args,**kwargs)
    fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    os.write(fd,(str(os.getpid())+'\\n').encode());os.close(fd)
daemon._write_pid_metadata=record
if mode=='fork':
    result=daemon.spawn_daemon_via_fork(socket_path=sock,pid_path=pid)
else:
    result=daemon.start_daemon(socket_path=sock,pid_path=pid,timeout_s=20)
print(json.dumps(result))
'''
    callers=[]
    workers=set()
    try:
        callers=[subprocess.Popen([sys.executable,'-c',script,mode,sock,pid,str(root/'workers')],env=environment(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for mode in modes]
        results=[]
        for caller in callers:
            out,err=caller.communicate(timeout=30)
            assert caller.returncode==0,err
            result=json.loads(out);results.append(result)
            if result.get('started'): workers.add(result['pid'])
        wait_for(lambda:daemon.daemon_status(socket_path=sock,pid_path=pid).get('running'))
        workers.add(daemon.daemon_status(socket_path=sock,pid_path=pid)['pid'])
        if (root/'workers').exists(): workers.update(int(p) for p in (root/'workers').read_text().splitlines())
        assert sum(r.get('spawned',False) or r.get('started',False) for r in results)==1,results
        assert len(workers)==1,(workers,results)
        winner=next(iter(workers))
        stopped=daemon.stop_daemon(socket_path=sock,pid_path=pid)
        assert stopped['stopped'] is True,stopped
        wait_for(lambda:not daemon._pid_alive(winner))
        restarted=daemon.start_daemon(socket_path=sock,pid_path=pid,timeout_s=20)
        if restarted.get('pid'): workers.add(restarted['pid'])
        assert restarted['started'] is True
        stopped=daemon.stop_daemon(socket_path=sock,pid_path=pid)
        assert stopped['stopped'] is True,stopped
    finally:
        for caller in callers:
            if caller.poll() is None: caller.kill()
            caller.wait(timeout=5)
        if (root/'workers').exists(): workers.update(int(p) for p in (root/'workers').read_text().splitlines())
        daemon.stop_daemon(socket_path=sock,pid_path=pid)
        for worker in workers:
            try: os.kill(worker,signal.SIGKILL)
            except ProcessLookupError: pass


@pytest.mark.parametrize('identity',['different','missing'])
def test_start_requires_matching_ping_identity(endpoint,monkeypatch,identity):
    root,sock,pid=endpoint
    fake='''
import os,socket,sys,time
from agentcad.daemon import _write_pid_metadata,_read_one_message,encode_message
sock,pid,identity=sys.argv[1:]
if identity == 'missing':
    with open(pid,'w') as f: f.write(str(os.getpid()))
    os.chmod(pid,0o600)
else:
    _write_pid_metadata(pid,pid=os.getpid(),instance_id='owner')
s=socket.socket(socket.AF_UNIX);s.bind(sock);os.chmod(sock,0o600);s.listen(8)
while True:
    c,_=s.accept();_read_one_message(c)
    response={'type':'pong'}
    if identity != 'missing': response['instance_id']=identity
    c.sendall(encode_message(response));c.close()
'''
    actual=subprocess.Popen
    workers=[]
    def launch(argv,**kwargs):
        worker=actual([sys.executable,'-c',fake,sock,pid,identity],**kwargs)
        workers.append(worker)
        return worker
    monkeypatch.setattr(subprocess,'Popen',launch)
    try:
        result=daemon.start_daemon(socket_path=sock,pid_path=pid,timeout_s=0.8)
        assert result['started'] is False,result
        assert 'identity' in result['message']
        assert not Path(sock).exists() and not Path(pid).exists()
    finally:
        for worker in workers:
            if worker.poll() is None: worker.kill()
            worker.wait(timeout=5)


@pytest.mark.parametrize('kind',['symlink','hardlink','public','directory'])
def test_unsafe_election_lock_is_preserved(endpoint,kind,monkeypatch):
    root,sock,pid=endpoint
    target=root/'keep';target.write_text('keep');target.chmod(0o600)
    lock=Path(sock+'.lock')
    if kind=='symlink': lock.symlink_to(target)
    elif kind=='hardlink': os.link(target,lock)
    elif kind=='directory': lock.mkdir()
    else: lock.write_text('keep');lock.chmod(0o644)
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('Unsafe lock spawned worker'))
    for call in (daemon.daemon_status,daemon.start_daemon,daemon.stop_daemon):
        result=call(socket_path=sock,pid_path=pid)
        assert result['error_kind']=='unsafe_daemon_state',result
        assert result['state_preserved'] is True
    assert target.read_text()=='keep' and os.path.lexists(lock)


def test_request_child_does_not_keep_dead_parent_elected(endpoint):
    from concurrent.futures import ThreadPoolExecutor
    root,sock,pid=endpoint
    script='''
import os,sys,time
from pathlib import Path
from agentcad import daemon
sock,pid,root=sys.argv[1:];root=Path(root)
def request(server,request,conn):
    (root/'child').write_text(str(os.getpid()))
    while not (root/'release').exists(): time.sleep(.01)
    conn.sendall(daemon.encode_message({'type':'result','output':'{}','exit_code':0}))
daemon._run_in_child=request
daemon.DaemonServer(socket_path=sock,pid_path=pid).serve()
'''
    server=subprocess.Popen([sys.executable,'-c',script,sock,pid,str(root)],env=environment())
    replacement=None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=None
        try:
            wait_for(lambda:daemon.daemon_status(socket_path=sock,pid_path=pid).get('running'))
            future=pool.submit(daemon.send_request,{'type':'run','argv':[]},socket_path=sock,response_timeout_s=10)
            wait_for(lambda:(root/'child').exists())
            server.kill();server.wait(timeout=5)
            replacement=daemon.start_daemon(socket_path=sock,pid_path=pid,timeout_s=8)
            assert replacement['started'] is True,replacement
            assert replacement['pid']!=server.pid
        finally:
            (root/'release').touch()
            if server.poll() is None: server.kill()
            server.wait(timeout=5)
            if future is not None: future.result(timeout=15)
            daemon.stop_daemon(socket_path=sock,pid_path=pid)


def test_auto_election_with_closed_stdin(endpoint):
    root,sock,pid=endpoint
    script='''
import json,os,sys
from agentcad import daemon
os.close(0)
print(json.dumps(daemon.spawn_daemon_via_fork(socket_path=sys.argv[1],pid_path=sys.argv[2])))
'''
    worker=None
    try:
        result=subprocess.run([sys.executable,'-c',script,sock,pid],env=environment(),capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)['spawned'] is True
        wait_for(lambda:daemon.daemon_status(socket_path=sock,pid_path=pid).get('running'))
        worker=daemon.daemon_status(socket_path=sock,pid_path=pid)['pid']
    finally:
        daemon.stop_daemon(socket_path=sock,pid_path=pid)
        if worker and daemon._pid_alive(worker): os.kill(worker,signal.SIGKILL)


def test_old_force_cleanup_cannot_remove_or_signal_replacement(endpoint,monkeypatch):
    root,sock,pid=endpoint
    old=daemon.start_daemon(socket_path=sock,pid_path=pid,timeout_s=20)
    assert old['started'] is True,old
    assert daemon.stop_daemon(socket_path=sock,pid_path=pid)['stopped'] is True
    new=daemon.start_daemon(socket_path=sock,pid_path=pid,timeout_s=20)
    try:
        assert new['started'] is True,new
        before=Path(pid).read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(daemon.os,'kill',lambda *a:pytest.fail('old cleanup signaled replacement'))
            assert daemon._force_kill(old['pid'],pid,sock) is None
        assert Path(pid).read_bytes()==before
        assert daemon.daemon_status(socket_path=sock,pid_path=pid)['pid']==new['pid']
    finally:
        daemon.stop_daemon(socket_path=sock,pid_path=pid)
