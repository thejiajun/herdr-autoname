import json
import unittest
from unittest.mock import patch
import switcher
import presentation
import context_data

class PickerTests(unittest.TestCase):
    def test_search_chinese_and_summary(self):
        agents=[dict(pane_id='w1:p1',workspace_label='创作者雷达',agent='codex',agent_status='blocked',_search='缓存刷新')]
        self.assertEqual(switcher.filtered(agents,'雷达 CODEX'),agents)
        self.assertEqual(switcher.filtered(agents,'等待确认'),agents)
        self.assertEqual(switcher.filtered(agents,'缓存'),agents)
        self.assertEqual(switcher.filtered(agents,'claude'),[])
    def test_width_and_control(self):
        self.assertEqual(presentation.clipped('中文abc',5),'中文a')
        self.assertEqual(presentation.clipped('a\x1bb',9),'ab')
        self.assertEqual(presentation.width(presentation.cell('测试中文',5)),5)
    def test_modified_keys(self):
        for key in ('\x1b\n','\x1b[106;7u','\x1b[27;7;106~'):
            self.assertEqual(switcher.decode(key),'next')
        for key in ('\x1b\x0b','\x1b[107;7u','\x1b[27;7;107~'):
            self.assertEqual(switcher.decode(key),'previous')
        self.assertIsNone(switcher.decode('\x1b[106;7:3u'))
        self.assertIsNone(switcher.decode('\x1b[57442;3:3u'))
        self.assertEqual(switcher.decode('\x1b'),'cancel')
        for seq,key in (('\x1b[A',switcher.curses.KEY_UP),('\x1bOB',switcher.curses.KEY_DOWN),('\x1b[1;2C',switcher.curses.KEY_RIGHT),('\x1b[D',switcher.curses.KEY_LEFT),('\x1b[57352u',switcher.curses.KEY_UP),('\x1b[5~',switcher.curses.KEY_PPAGE)):
            self.assertEqual(switcher.decode(seq),key)
        self.assertIsNone(switcher.decode('\x1b[999;7u'))
    @patch('switcher.context_data.load_cache',return_value={})
    @patch('switcher.cli')
    def test_sidebar_name_and_foreground_directory(self,cli,cache):
        cli.return_value={'snapshot':{'panes':[{'agent':'codex','pane_id':'w1:p1','workspace_id':'w1','label':'部署更新到3台机器','cwd':'/old','foreground_cwd':'/actual'}],'workspaces':[{'workspace_id':'w1','label':'测试'}]}}
        row=switcher.fetch_agents()[0]
        self.assertEqual(row['display_name'],'部署更新到3台机器')
        self.assertEqual(row['project_dir'],'/actual')

class ImmediatePool:
    def __init__(self,**kwargs): pass
    def submit(self,fn,*args,**kwargs):
        from concurrent.futures import Future
        future=Future()
        try: future.set_result(fn(*args,**kwargs))
        except Exception as exc: future.set_exception(exc)
        return future
    def shutdown(self,**kwargs): pass

class Window:
    def __init__(self,width): self.width=width; self.drawn=[]
    def getmaxyx(self): return (40,self.width)
    def addstr(self,y,x,text,attr):
        assert 0<=y<40 and 0<=x<self.width
        self.drawn.append(text)
    def timeout(self,*args): pass
    def keypad(self,*args): pass
    def erase(self): pass
    def refresh(self): pass
    def bkgd(self,*args): pass

class InteractionTests(unittest.TestCase):
    def run_ui(self,keys,width=46):
        agents=[dict(pane_id=f'w{i}:p1',workspace_id=f'w{i}',workspace_label=f'项目{i}',display_name=f'部署任务{i}',project_dir='/actual/project',agent='codex',agent_status='working',focused=i==1) for i in (1,2,3)]
        agents[2]['_search']='jira 登录报错'
        win=Window(width)
        with patch('switcher.fetch_agents',return_value=agents),patch('switcher.read_details',return_value=('screen',{'request':'测试请求','reply':'测试回复'})),patch('switcher.cli') as cli,patch('switcher.keypress',side_effect=keys),patch('switcher.concurrent.futures.ThreadPoolExecutor',ImmediatePool),patch.multiple('switcher.curses',curs_set=lambda *_:None,set_escdelay=lambda *_:None,start_color=lambda:None,use_default_colors=lambda:None,init_pair=lambda *_:None,color_pair=lambda *_:0),patch.dict('os.environ',{'HERDR_SWITCHER_DIRECTION':'0'}):
            result=switcher.ui(win)
            return result,win,cli
    def test_narrow_wide_tables_focus(self):
        for width in (46,160):
            result,win,cli=self.run_ui([None,'next',None,'next',None,'previous',None,'\n'],width=width)
            self.assertEqual(result,'w2:p1')
            cli.assert_called_once_with('agent','get','w2:p1')
            self.assertTrue(any('AGENT' in s for s in win.drawn))
            self.assertTrue(any('最新对话' in s for s in win.drawn))
    def test_popup_stays_open_without_input(self):
        with self.assertRaises(StopIteration): self.run_ui([None,None,None])
    def test_jk_navigate(self):
        result,_,_=self.run_ui(['j','j','k','\n'])
        self.assertEqual(result,'w2:p1')
    def test_typing_starts_search_then_jk_are_letters(self):
        result,_,_=self.run_ui(['登','录','\n'])
        self.assertEqual(result,'w3:p1')
        result,_,_=self.run_ui(['/','j','i','r','a','\n'])
        self.assertEqual(result,'w3:p1')
    def test_escape_clears_search_then_closes(self):
        result,_,cli=self.run_ui(['登','cancel','k','\n'])
        self.assertEqual(result,'w2:p1')
        result,_,cli=self.run_ui(['cancel'])
        self.assertIsNone(result);cli.assert_not_called()

class ContextTests(unittest.TestCase):
    def test_stale_herdr_bin_path_falls_back_to_path(self):
        with patch.dict('os.environ', {'HERDR_BIN_PATH':'/missing/herdr'}), patch('context_data.shutil.which', return_value='/usr/local/bin/herdr'):
            self.assertEqual(context_data.herdr_bin(), '/usr/local/bin/herdr')
    def test_no_previous_reply_for_new_request(self):
        with patch('context_data.messages',return_value=([('user','旧请求'),('assistant','旧回复'),('user','新请求')],'native')):
            s=context_data.signals({})
        self.assertEqual(s['request'],'新请求');self.assertEqual(s['reply'],'')
    def test_ignore_injected_and_analysis(self):
        row={'type':'response_item','payload':{'type':'message','role':'assistant','channel':'analysis','content':[{'type':'output_text','text':'private'}]}}
        self.assertIsNone(context_data.parse_row(row,'codex'))
        self.assertFalse(context_data.usable('user','# AGENTS.md instructions\nignore me'))
    def test_session_identity_does_not_collide(self):
        a={'agent':'codex','pane_id':'w1:p1','agent_session':{'value':'a'}}
        b={**a,'agent_session':{'value':'b'}}
        self.assertNotEqual(context_data.identity(a),context_data.identity(b))
    def test_summary_validation(self):
        payload={'summaries':[dict(id='known',title='标题',request='请求',progress='报告',next='未提及'),dict(id='unknown',title='错误')]}
        self.assertEqual(list(context_data.validate(payload,{'known'})),['known'])


class SessionCwdTests(unittest.TestCase):
    def check(self,kind,rows,expected):
        import tempfile,pathlib
        with tempfile.NamedTemporaryFile('w',suffix='.jsonl',delete=False) as f:
            f.write('\n'.join(json.dumps(r) for r in rows))
        with patch('context_data.session_path',return_value=pathlib.Path(f.name)):
            self.assertEqual(context_data.session_cwd({'agent':kind,'agent_session':{'value':'x'}}),expected)
    def test_claude_uses_latest_row_cwd(self):
        self.check('claude',[{'cwd':'/launch'},{'cwd':'/moved'},{'type':'last-prompt'}],'/moved')
    def test_codex_uses_latest_turn_context(self):
        self.check('codex',[{'type':'session_meta','payload':{'cwd':'/launch'}},{'type':'turn_context','payload':{'cwd':'/moved'}},{'type':'response_item','payload':{}}],'/moved')
    def test_other_agents_fall_back(self):
        self.assertIsNone(context_data.session_cwd({'agent':'pi'}))


class JumpTests(unittest.TestCase):
    @patch('switcher.context_data.state_dir')
    @patch('switcher.cli')
    @patch('switcher.time.sleep')
    @patch('switcher.os.kill',side_effect=ProcessLookupError)
    def test_focus_runs_only_after_popup_exit(self,kill,sleep,cli,state):
        cli.side_effect=[{'agent':{'workspace_id':'w1','tab_id':'w1:t1'}},{},{},{'agent':{'focused':True}}]
        switcher.finish_jump(123,'w1:p1')
        self.assertEqual([c.args for c in cli.call_args_list],[('agent','get','w1:p1'),('workspace','focus','w1'),('tab','focus','w1:t1'),('agent','focus','w1:p1')])


class SortTests(unittest.TestCase):
    rows=[{'pane_id':'a1','project_dir':'/b','_active':5},
          {'pane_id':'b1','project_dir':'/a','_active':1},
          {'pane_id':'a2','project_dir':'/b','_active':9},
          {'pane_id':'b2','project_dir':'/a','_active':3}]
    def test_recent_newest_first(self):
        self.assertEqual([a['pane_id'] for a in switcher.order_agents(self.rows,'recent')],['a2','a1','b2','b1'])
    def test_folder_groups_then_recent(self):
        self.assertEqual([a['pane_id'] for a in switcher.order_agents(self.rows,'folder')],['b2','b1','a2','a1'])
