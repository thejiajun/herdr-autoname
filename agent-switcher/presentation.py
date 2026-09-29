"""Terminal-cell-aware table and conversation detail layout."""
import curses
import time
import os
import unicodedata

STATUS={'working':('▶','运行中',2),'blocked':('!','待确认',3),'done':('✓','已完成',4),'idle':('○','空闲',5),'unknown':('?','未知',5)}

VS16='\ufe0f'
WIDE_EMOJI={'🎙':'🎤','🏷':'🔖','🏛':'🏦','🖥':'💻','🗂':'📁','🛠':'🔧','🗓':'📅','🕹':'🎮','🖼':'🎨','🗺':'🧭','🏗':'🚧','🕵':'🔍','🗃':'📦','🖨':'📄','🛡':'🔒','🌡':'📈','🕸':'🌐','🗜':'📦','🎞':'🎬','🖌':'🎨','🖊':'📝','🗒':'📝','🗄':'📦','🏖':'🌴','🏔':'🗻','🛰':'📡'}

def width(text):
    return sum(0 if unicodedata.combining(c) or c==VS16 else 2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in text)

def clipped(text,n):
    out=[]; used=0
    for c in str(text).replace('\t','    '):
        # Drop VS16: terminals then draw text-style emoji at the width curses expects.
        if unicodedata.category(c).startswith('C') or c==VS16: continue
        # curses counts text-presentation emoji (🎙) as 1 cell but Herdr draws 2, shifting the row.
        # Swap in a wide look-alike both agree on, or blank 2 cells to keep columns aligned.
        if ord(c)>=0x1F000 and unicodedata.east_asian_width(c)=='N':
            if used+2>n: break
            out.append(WIDE_EMOJI.get(c,'  ')); used+=2; continue
        cells=0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W','F') else 1
        if used+cells>n: break
        out.append(c); used+=cells
    return ''.join(out)

def cell(text,n):
    text=str(text).replace('\n',' ').replace('\r',' ').replace('\t',' ')
    # Measure the clipped form: it may pad narrow emoji, so raw width can undercount.
    value=clipped(text,10**6)
    if width(value)>n: value=clipped(text,max(0,n-1))+'…'
    return value+' '*max(0,n-width(value))

def wrap(text,n):
    lines=[]
    for paragraph in str(text).splitlines() or ['']:
        current=''
        for char in paragraph:
            if unicodedata.category(char).startswith('C'): continue
            if width(current+char)>n:
                lines.append(current); current=''
            current+=char
        lines.append(current)
    return lines

def put(win,y,x,text,n,attr=0):
    h,w=win.getmaxyx()
    if 0<=y<h and 0<=x<w:
        try: win.addstr(y,x,clipped(text,min(n,w-x-1)),attr)
        except curses.error: pass

def age(timestamp):
    seconds=max(0,int(time.time()-timestamp))
    if seconds<60: return '刚刚'
    if seconds<3600: return f'{seconds//60} 分钟前'
    return f'{seconds//3600} 小时前'

def detail_lines(agent,parsed,cached,terminal,n,view):
    if view=='terminal':
        return [(s,False) for line in terminal.splitlines() for s in wrap(line,n)]
    result=[]
    def heading(s): result.append((s,True))
    def body(label,value,style=False):
        result.extend((s,style) for s in wrap(label+value,n)); result.append(('',False))
    heading('最新对话')
    def excerpt(text):
        limit=max(70,min(240,n*3))
        return text if len(text)<=limit else text[:limit//3]+' … '+text[-(limit*2//3):]
    # Raw excerpts are secondary to the summary, so render them dimmed.
    body('你：',excerpt(parsed.get('request') or '暂无可读请求'),'dim')
    body('Agent：',excerpt(parsed.get('reply') or '这次请求尚无可读回复'),'dim')
    heading('对话总结 · 后台生成')
    summary=cached.get('summary')
    if summary:
        stale=cached.get('summary_fingerprint')!=parsed.get('fingerprint')
        result.append((age(cached['summary_at'])+(' · 有新对话，待更新' if stale else ' · 已覆盖当前对话'),False))
        for label,key in [('请求：','request'),('进度：','progress'),('待办：','next')]: body(label,summary.get(key,'未提及'))
    else:
        body('',cached.get('error') or '等待后台生成；可先看上方最新对话')
    return result

def draw(win,rows,index,total,query,agent,parsed,cached,terminal,scroll,error,searching,view,sort='folder'):
    h,w=win.getmaxyx(); win.erase()
    title='AGENTS  /  '+str(len(rows))+' 个匹配'
    put(win,0,1,title,w-2,curses.A_BOLD|curses.color_pair(1))
    # Segmented sort switch: active mode bracketed and highlighted, the other dim.
    x=width(title)+5
    put(win,0,x,'排序',w-x-1,curses.A_DIM); x+=5
    for label,mode in (('目录','folder'),('最近','recent')):
        active=mode==sort
        put(win,0,x,'['+label+']' if active else ' '+label+' ',w-x-1,curses.A_BOLD|curses.color_pair(1) if active else curses.A_DIM); x+=7
    put(win,0,x,'←→ 切换',w-x-1,curses.A_DIM)
    prompt=(query+'▏') if searching else (query or '直接输入搜索：名称、目录、对话内容')
    put(win,1,1,'搜索  '+prompt,w-2,curses.A_BOLD if searching or query else curses.A_DIM)
    if h<16 or w<32:
        put(win,3,1,'请放大至 32 列 × 16 行 · Esc 关闭',w-2); return 0
    split=w>=135
    table_width=min(90,max(68,w*3//5)) if split else w-2
    count=h-8 if split else max(2,min(9,(h-11)//2))
    # Narrow windows keep the status icon only; the folder column scales with width.
    status_width=8 if table_width>=44 else 2
    dir_width=max(10,min(24,table_width//4))
    # Columns: "› " + status │ name │ dir
    name_width=table_width-2-status_width-dir_width-6
    def row(marker,status,name,folder):
        return marker+cell(status,status_width)+' │ '+cell(name,name_width)+' │ '+cell(folder,dir_width)
    put(win,3,1,row('  ','状态' if status_width>2 else '','Agent 名称','项目目录'),table_width,curses.A_BOLD|curses.color_pair(1))
    put(win,4,1,'─'*(2+status_width+1)+'┼'+'─'*(name_width+2)+'┼'+'─'*(dir_width+1),table_width,curses.A_DIM)
    start=max(0,min(index-count//2,max(0,len(rows)-count)))
    for pos,a in enumerate(rows[start:start+count],start):
        y=5+pos-start; state=STATUS.get(a.get('agent_status'),STATUS['unknown'])
        selected=pos==index; attr=curses.A_REVERSE if selected else 0
        label=a.get('display_name') or a.get('workspace_label') or a['pane_id']
        if a.get('focused'): label='• '+label
        state_label=state[0]+(' '+state[1] if status_width>2 else '')
        folder=os.path.basename(a.get('project_dir','').rstrip('/')) or '—'
        put(win,y,1,row('› ' if selected else '  ',state_label,label,folder),table_width,attr)
        if not selected: put(win,y,3,state_label,status_width,curses.color_pair(state[2]))
    if not rows: put(win,5,3,'没有匹配结果',table_width-2)
    if split:
        x,top,pw=table_width+3,3,w-table_width-5
        for y in range(3,h-3): put(win,y,table_width+1,'│',1,curses.A_DIM)
    else:
        x,top,pw=1,6+count,w-2
        put(win,top-1,1,'─'*(w-2),w-2,curses.A_DIM)
    if agent:
        # Key-value header: label │ value, values wrap inside their column.
        vw=max(8,pw-7)
        source='终端快照 · 非完整对话' if view=='terminal' else parsed.get('source','正在读取原生对话…')
        meta=[]
        for label,value,style in (('名称',agent.get('display_name') or agent.get('workspace_label',''),True),
                                  ('目录',agent.get('project_dir') or agent.get('cwd') or '未提供',False),
                                  ('来源',source,False)):
            for i,part in enumerate(wrap(value,vw)):
                meta.append((cell(label if i==0 else '',4),part,style))
        for i,(label,value,style) in enumerate(meta):
            put(win,top+i,x,label+' │ ',7,curses.A_BOLD|curses.color_pair(1))
            put(win,top+i,x+7,value,vw,curses.A_BOLD|curses.color_pair(1) if style else 0)
        put(win,top+len(meta),x,'─'*5+'┴'+'─'*(pw-6),pw,curses.A_DIM)
        body_top=top+len(meta)+2
        capacity=max(1,h-body_top-3)
        lines=detail_lines(agent,parsed,cached,terminal,pw,view)
        scroll=min(scroll,max(0,len(lines)-capacity))
        for i,(line,style) in enumerate(lines[scroll:scroll+capacity]):
            attr=curses.A_BOLD|curses.color_pair(1) if style is True else curses.color_pair(5) if style=='dim' else 0
            put(win,body_top+i,x,line,pw,attr)
        if len(lines)>capacity: put(win,h-3,x,f'{scroll+1}–{min(scroll+capacity,len(lines))}/{len(lines)} · PgUp/PgDn',pw,curses.A_DIM)
    else: scroll=0
    hint='输入搜索 · Enter 跳转 · Esc 清空' if searching else 'Enter 跳转 · 输入即搜索 · Tab 终端/对话'
    put(win,h-2,1,error or hint,w-2,curses.A_BOLD)
    put(win,h-1,1,('↑↓ 选择 · Ctrl+U 清空' if searching else 'J/K ↑↓ 选择 · j/k 开头先按 / · Esc 关闭'),w-2,curses.A_DIM)
    return scroll
