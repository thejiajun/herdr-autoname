#!/usr/bin/env python3
"""Dependency-free Herdr popup picker; previews never send input to agents."""
import concurrent.futures
import curses
import json
import locale
import os
import re
import subprocess
import sys
import time
import context_data
import presentation
import input_source

STATUS = {'working':'运行中', 'blocked':'等待确认', 'done':'已完成', 'idle':'空闲', 'unknown':'未知'}

def cli(*args, raw=False):
    p = subprocess.run([context_data.herdr_bin(), *args], capture_output=True, text=True, timeout=5)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip() or 'Herdr 调用失败')
    if raw:
        return p.stdout
    data = json.loads(p.stdout)
    if 'error' in data:
        raise RuntimeError(str(data['error']))
    return data.get('result', data)

SORTS=('folder','recent')

def order_agents(agents,mode):
    # Most recent conversation first; folder mode then groups by working dir (stable sort keeps recency inside).
    recent=sorted(agents,key=lambda a:-(a.get('_active') or 0))
    if mode!='folder': return recent
    return sorted(recent,key=lambda a:(a.get('project_dir') or '~').casefold())

def load_sort():
    try: mode=json.loads((context_data.state_dir()/'prefs.json').read_text()).get('sort')
    except (OSError,ValueError,AttributeError): mode=None
    return mode if mode in SORTS else 'folder'

def save_sort(mode):
    try: (context_data.state_dir()/'prefs.json').write_text(json.dumps({'sort':mode}))
    except OSError: pass

def fetch_agents():
    snapshot=cli('api','snapshot')['snapshot']
    spaces={w['workspace_id']:w['label'] for w in snapshot['workspaces']}
    panes={p['pane_id']:p for p in snapshot['panes']}
    agents=[{**panes.get(a['pane_id'],{}),**a} for a in snapshot.get('agents',snapshot['panes']) if a.get('agent')]
    cache=context_data.load_cache()
    for a in agents:
        a['workspace_label']=(a.get('tokens') or {}).get('workspace_plain') or spaces.get(a['workspace_id'],a['workspace_id'])
        a['display_name']=a.get('label') or a.get('name') or a.get('terminal_title_stripped') or a['workspace_label']
        a['project_dir']=context_data.session_cwd(a) or a.get('foreground_cwd') or a.get('cwd') or ''
        a['_active']=context_data.session_mtime(a) or 0
        a['_cached']=context_data.cached_for(a,cache)
        a['_summary']=a['_cached'].get('summary',{})
        a['_search']=context_data.search_text(a)+' '+json.dumps(a['_summary'],ensure_ascii=False)
    return agents

def filtered(agents, query):
    words = query.casefold().split()
    return [a for a in agents if all(word in (' '.join(str(a.get(k,'')) for k in
        ('display_name','project_dir','terminal_title_stripped','workspace_label','agent','name','cwd','pane_id','agent_status','_search'))+' '+STATUS.get(a.get('agent_status'),'未知')).casefold() for word in words)]

def decode(sequence):
    if sequence=='\x1b\n': return 'next'
    if sequence=='\x1b\x0b': return 'previous'
    match=re.fullmatch(r'\x1b\[(\d+)(?::[\d:]*)?(?:;(\d+)(?::([123]))?)?u',sequence)
    if match:
        code,mods,kind=int(match[1]),int(match[2] or 1)-1,int(match[3] or 1)
        if kind==3: return None
        if code in (106,107) and mods & 6==6: return 'next' if code==106 else 'previous'
        if code==27: return 'cancel'
        if code==13: return '\n'
        if code==9: return '\t'
        if code==127: return '\x7f'
        if code==57352: return curses.KEY_UP
        if code==57353: return curses.KEY_DOWN
        if code==57350: return curses.KEY_LEFT
        if code==57351: return curses.KEY_RIGHT
        if code==57354: return curses.KEY_PPAGE
        if code==57355: return curses.KEY_NPAGE
        if code==117 and mods & 4: return '\x15'
        if code<57344 and not mods & 6:
            try: return chr(code).upper() if mods & 1 else chr(code)
            except ValueError: return None
        return None
    # Legacy cursor keys (CSI or SS3, optionally with modifiers) when the terminal ignores Kitty mode.
    match=re.fullmatch(r'\x1b(?:\[(?:1;\d+)?|O)([ABCD])',sequence)
    if match: return {'A':curses.KEY_UP,'B':curses.KEY_DOWN,'C':curses.KEY_RIGHT,'D':curses.KEY_LEFT}[match[1]]
    if sequence=='\x1b[5~': return curses.KEY_PPAGE
    if sequence=='\x1b[6~': return curses.KEY_NPAGE
    match=re.fullmatch(r'\x1b\[27;7;(106|107)~',sequence)
    if match: return 'next' if match[1]=='106' else 'previous'
    return 'cancel' if sequence=='\x1b' else None

def keypress(win):
    try:
        key = win.get_wch()
    except curses.error:
        return None
    if key != '\x1b':
        return key
    sequence = key
    win.timeout(25)
    try:
        while len(sequence)<32:
            try:
                nxt = win.get_wch()
            except curses.error:
                break
            if not isinstance(nxt,str):
                break
            sequence += nxt
            if len(sequence)==2 and nxt not in '[O':
                break
            # SS3 is one final byte; CSI ends at any byte in 0x40-0x7E.
            if sequence.startswith('\x1bO') and len(sequence)==3:
                break
            if len(sequence)>2 and sequence.startswith('\x1b[') and '@'<=nxt<='~':
                break
    finally:
        win.timeout(80)
    return decode(sequence)

def ui(win):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    for pair,color in ((1,curses.COLOR_CYAN),(2,curses.COLOR_BLUE),(3,curses.COLOR_YELLOW),(4,curses.COLOR_GREEN),(5,curses.COLOR_WHITE)):
        curses.init_pair(pair,color,-1)
    win.bkgd(' ', curses.A_NORMAL)
    curses.set_escdelay(25)
    win.keypad(True)
    win.timeout(80)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    (context_data.state_dir()/'popup-status.json').write_text(json.dumps({'pid':os.getpid(),'ssh':bool(os.environ.get('SSH_CONNECTION')),'direction':os.environ.get('HERDR_SWITCHER_DIRECTION')}))
    view='conversation'
    # Navigation mode by default (j/k move); '/' enters search mode where letters type.
    searching=False
    sort=load_sort()
    parsed={}
    agents, query, selected = [], '', None
    preview, preview_id, scroll, error = '', None, 0, ''
    listing, reading = pool.submit(fetch_agents), None
    list_due, read_due, initial = time.monotonic()+3, 0, True
    try:
        while True:
            now = time.monotonic()
            if listing and listing.done():
                try:
                    agents = listing.result()
                    error = ''
                except Exception as exc:
                    error = str(exc)
                listing, list_due = None, now+3
            if not listing and now>=list_due:
                listing = pool.submit(fetch_agents)
            rows = filtered(order_agents(agents,sort),query)
            ids = [a['pane_id'] for a in rows]
            if selected not in ids:
                selected = next((a['pane_id'] for a in rows if a.get('focused')),ids[0] if ids else None)
            if initial and ids:
                direction = int(os.environ.get('HERDR_SWITCHER_DIRECTION','0'))
                selected = ids[(ids.index(selected)+direction)%len(ids)]
                initial = False
            index = ids.index(selected) if selected else 0
            agent = rows[index] if rows else None
            if reading and reading.done():
                try:
                    output, parsed_output = reading.result()
                except Exception as exc:
                    output, parsed_output = '预览不可用：'+type(exc).__name__,{}
                if reading_target==selected:
                    preview, preview_id, read_due = output, selected, now+2
                    parsed=parsed_output
                reading = None
            if selected != preview_id:
                preview, scroll, parsed = '正在读取终端快照…', 0, {}
            if selected and not reading and (selected!=preview_id or now>=read_due):
                reading_target = selected
                reading = pool.submit(read_details,agent)
            h,w=win.getmaxyx()
            cached=(agent or {}).get('_cached',{})
            shown_parsed=parsed or cached.get('parsed',{})
            scroll=presentation.draw(win,rows,index,len(agents),query,agent,shown_parsed,cached,preview,scroll,error,searching,view,sort)
            win.refresh()
            key = keypress(win)
            if key=='cancel' and (searching or query):
                searching, query = False, ''
                continue
            if key in ('cancel','\x03'):
                return None
            if key in ('\n','\r',curses.KEY_ENTER) and agent:
                try:
                    cli('agent','get',agent['pane_id'])
                    return agent['pane_id']
                except Exception as exc:
                    error, list_due = '切换失败：'+str(exc),0
            elif key=='\t':
                view='terminal' if view=='conversation' else 'conversation'
                scroll=0
            elif not searching and key=='/':
                searching=True
            # Outside search, j/k navigate; any other printable key starts a search.
            elif (key in ('next',curses.KEY_DOWN,'\x0e') or not searching and key=='j') and ids:
                selected = ids[(index+1)%len(ids)]
            elif (key in ('previous',curses.KEY_UP,'\x10') or not searching and key=='k') and ids:
                selected = ids[(index-1)%len(ids)]
            elif key in (curses.KEY_LEFT,curses.KEY_RIGHT):
                sort=SORTS[(SORTS.index(sort)+1)%len(SORTS)]
                save_sort(sort)
            elif key==curses.KEY_PPAGE:
                scroll += max(1,h-11)
            elif key==curses.KEY_NPAGE:
                scroll = max(0,scroll-max(1,h-11))
            elif key in (curses.KEY_BACKSPACE,'\x7f','\b'):
                query = query[:-1]
                searching = bool(query)
            elif key=='\x15':
                query, searching = '', False
            elif isinstance(key,str) and len(key)==1 and key.isprintable():
                searching = True
                query += key
    finally:
        pool.shutdown(wait=False,cancel_futures=True)

def read_details(agent):
    parsed=context_data.signals(agent)
    try: screen=cli('agent','read',agent['pane_id'],'--source','visible','--lines','160',raw=True)
    except Exception: screen='终端快照暂不可用'
    return screen,parsed

def terminfo_without_rep():
    """Herdr's popup ignores REP (CSI b), so ncurses' run compression drops box-drawing lines."""
    term=os.environ.get('TERM') or 'xterm-256color'
    target=context_data.state_dir()/'terminfo'
    try:
        if not any(target.glob('*/'+term)):
            source=subprocess.run(['infocmp','-x',term],capture_output=True,text=True,check=True,timeout=5).stdout
            subprocess.run(['tic','-x','-o',str(target),'-'],input=re.sub(r'\brep=[^,]*,\s*','',source),capture_output=True,text=True,check=True,timeout=5)
        os.environ['TERMINFO']=str(target)
    except (OSError,subprocess.SubprocessError):
        pass

def run_ui(win):
    # Disambiguate escape codes only; key-release events are not needed.
    sys.stdout.write('\x1b[>1u'); sys.stdout.flush()
    try: return ui(win)
    finally:
        sys.stdout.write('\x1b[<u'); sys.stdout.flush()

def finish_jump(parent,target):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        try: os.kill(parent,0)
        except ProcessLookupError: break
        time.sleep(0.03)
    else: raise RuntimeError('Popup did not exit; jump cancelled')
    # Herdr must finish removing the modal before changing its client projection.
    time.sleep(0.12)
    agent=cli('agent','get',target)['agent']
    cli('workspace','focus',agent['workspace_id'])
    cli('tab','focus',agent['tab_id'])
    result=cli('agent','focus',target)
    (context_data.state_dir()/'last-jump.json').write_text(json.dumps({'target':target,'focused':result.get('agent',{}).get('focused'),'at':time.time()}))

def main():
    if '--finish' in sys.argv:
        try: finish_jump(int(sys.argv[-2]),sys.argv[-1])
        except Exception as exc:
            (context_data.state_dir()/'last-jump.json').write_text(json.dumps({'target':sys.argv[-1],'error':type(exc).__name__,'at':time.time()}))
            raise
    elif '--open' in sys.argv:
        cli('plugin','pane','open','--plugin','thejiajun.agent-switcher','--entrypoint','picker','--env','HERDR_SWITCHER_DIRECTION='+sys.argv[-1],'--focus')
    elif '--check' in sys.argv:
        agents = fetch_agents()
        if agents:
            cli('agent','read',agents[0]['pane_id'],'--source','visible','--lines','3',raw=True)
        print(f'OK: {len(agents)} agents; workspace labels and terminal preview available')
    else:
        locale.setlocale(locale.LC_ALL,'')
        # Chinese IMEs swallow bare j/k into composition; use ASCII while the popup is open.
        terminfo_without_rep()
        with input_source.AsciiInput():
            target=curses.wrapper(run_ui)
        if target:
            subprocess.Popen([sys.executable,os.path.abspath(__file__),'--finish',str(os.getpid()),target],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)

if __name__=='__main__':
    try:
        main()
    except (RuntimeError,subprocess.TimeoutExpired) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
