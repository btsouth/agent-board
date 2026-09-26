import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentboard import backend, hermes_live, wowclient, hypr

class OverlayRegression(unittest.TestCase):
    def test_historical_question_does_not_need_attention(self):
        data = {'sessions': [{'id':'old', 'provider':'hermes', 'status':'needs'}]}
        result = hermes_live.enrich(data, [])
        self.assertEqual(result['sessions'][0]['status'], 'reply')
        self.assertEqual(data['sessions'][0]['status'], 'needs')

    def test_only_outstanding_requests_create_cards(self):
        row = {'id':'one', 'provider':'hermes', 'status':'working'}
        live = [{'session_key':'one', 'status':'waiting', 'open_requests':[
            {'id':'req', 'method':'clarify', 'params':{'questions':[{'qid':'q1','question':'Which?', 'choices':['A','B']}]}}
        ]}]
        result = hermes_live.enrich({'sessions':[row]},live)['sessions'][0]
        self.assertEqual(result['user_input_questions'][0]['id'],'q1')
        self.assertEqual(result['user_input_questions'][0]['options'],['A','B'])
        self.assertEqual(result['user_input_request_id'],'req')

    def test_hermes_reply_streams_before_the_store_has_it(self):
        stream = hermes_live.Stream()
        event = lambda seq, kind, **payload: {'seq': seq, 'type': kind, 'payload': payload}
        stream.apply({'epoch': 1, 'latest_seq': 3, 'events': [
            event(1, 'message.start'), event(2, 'message.delta', text='Hel'), event(3, 'message.delta', text='lo'),
        ]})
        self.assertEqual(stream.last_seen, 3)
        row = {'id': 'one', 'provider': 'hermes', 'status': 'working',
               'conversation': [{'id': '1', 'role': 'user', 'text': 'hi'}]}
        live = [{'session_key': 'one', 'status': 'working'}]
        result = hermes_live.enrich({'sessions': [row]}, live, {'one': stream})['sessions'][0]
        self.assertEqual(result['conversation'][-1]['text'], 'Hello')
        self.assertTrue(result['conversation'][-1]['streaming'])

        # Completed, and then written to the store: shown once, not twice.
        stream.apply({'epoch': 1, 'latest_seq': 4, 'events': [event(4, 'message.complete', text='Hello')]})
        row['conversation'].append({'id': '2', 'role': 'agent', 'text': 'Hello'})
        result = hermes_live.enrich({'sessions': [row]}, live, {'one': stream})['sessions'][0]
        self.assertEqual([m['text'] for m in result['conversation']], ['hi', 'Hello'])

    def test_a_new_backend_epoch_restarts_the_stream(self):
        stream = hermes_live.Stream()
        stream.apply({'epoch': 1, 'latest_seq': 9, 'events': [{'type': 'message.delta', 'payload': {'text': 'old'}}]})
        stream.apply({'epoch': 2, 'latest_seq': 1, 'events': [{'type': 'message.delta', 'payload': {'text': 'new'}}]})
        self.assertEqual(stream.current, 'new')
        self.assertEqual(stream.last_seen, 1)

    def test_immediate_hermes_uses_steer_without_interrupt(self):
        sessions = {'sessions':[{'session_key':'stored','id':'runtime','status':'working'}]}
        with patch.object(backend,'call',side_effect=[sessions,sessions,{'status':'queued'}]) as call:
            result = backend.submit_reply('stored','correction',backend={'url':'test'},delivery='immediate')
        self.assertEqual(result,{'status':'steered'})
        self.assertEqual([c.args[0] for c in call.call_args_list], ['session.active_list','session.active_list','session.steer'])

    def test_queued_hermes_send_cannot_interrupt_busy_race(self):
        with patch.object(backend,'_runtime_session_id',return_value='runtime'), patch.object(backend,'call',return_value={'status':'queued'}) as call:
            backend.submit_reply('stored','later',backend={'url':'test'},delivery='queued')
        self.assertTrue(call.call_args.args[1]['queued'])

    def test_uncertain_send_never_launches_duplicate_cli(self):
        with patch.object(backend,'submit_reply',side_effect=RuntimeError('connection lost')), patch.object(backend,'submit_reply_cli') as cli:
            result = wowclient.dispatch_live({'kind':'reply','provider':'hermes','session_id':'one','text':'hello','delivery':'queued'},state={})
        self.assertFalse(result['ok']); cli.assert_not_called()

    def test_expired_hermes_request_is_not_acknowledged(self):
        with patch.object(backend,'find_backend',return_value={'url':'test'}), patch.object(backend,'_runtime_session_id',return_value='runtime'), patch.object(backend,'call',return_value={'open_requests':[]}):
            with self.assertRaisesRegex(RuntimeError,'expired'):
                hermes_live.respond({'kind':'approve','session_id':'one','text':'gone'})

    def test_monitor_id_is_not_array_index(self):
        with patch.object(hypr,'_monitors',return_value=[{'id':3,'name':'left'},{'id':8,'name':'game'}]):
            self.assertEqual(hypr._monitor_for_client({'monitor':8})['name'],'game')

    def test_repin_is_idempotent(self):
        with patch.object(hypr,'available',return_value=True), patch.object(hypr,'find_window',return_value={'address':'0x123','floating':True,'pinned':True}), patch.object(hypr,'_dispatch') as dispatch, patch.object(hypr,'_place',return_value={}):
            result = hypr.pin(width=286,height=46)
        dispatch.assert_not_called(); self.assertTrue(result['pinned'])

    def test_saved_position_is_preserved_and_clamped_on_small_display(self):
        monitor = {'id':8,'name':'game','width':2560,'height':1440,'scale':1.25,'x':0,'y':0,'reserved':[0,24,0,0]}
        with patch.object(hypr,'_game_target',return_value=None), patch.object(hypr,'_monitors',return_value=[monitor]), patch.object(hypr,'_target_monitor',return_value=monitor), patch.object(hypr,'_dispatch',return_value=(True,'')):
            placed = hypr._place('address:0x1',286,46,24,{},position=(800,300))
            self.assertEqual((placed['x'],placed['y']),(800,300))
            placed = hypr._place('address:0x1',760,640,24,{},position=(3000,-100))
            self.assertEqual((placed['x'],placed['y']),(1288,24))

    def test_multi_question_answers_reach_t3_unchanged(self):
        answers = {'q1':'Yes','q2':'Second answer'}
        with patch.object(wowclient.t3,'dispatch',return_value={'ok':True}) as dispatch, patch.object(wowclient,'_save_state'):
            result = wowclient.dispatch_live({'kind':'answer','provider':'t3','session_id':'thread-one','request_id':'request-one','answers':answers},state={})
        self.assertTrue(result['ok'])
        self.assertEqual(dispatch.call_args.args[0]['answers'],answers)
        self.assertEqual(dispatch.call_args.args[0]['request_id'],'request-one')

if __name__ == '__main__': unittest.main()
