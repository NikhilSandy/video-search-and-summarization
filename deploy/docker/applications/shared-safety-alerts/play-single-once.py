#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
import argparse, json, os, signal, subprocess, time, traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
BUNDLE=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description="Publish one prepared video once and audit a stream-specific rule on the existing stack.")
parser.add_argument('--run-dir',type=Path,required=True)
parser.add_argument('--mediamtx',type=Path,required=True)
args=parser.parse_args()
BUILD=args.run_dir.resolve()
CFG=json.loads((BUILD/'input.json').read_text())
DETECTOR=json.loads((BUILD/'detector.json').read_text())
SOURCE_PATH=CFG['source_url'].rsplit('/',1)[-1]
AUDIT_PREFIX='/tmp/vss-playback-'+BUILD.name

STATE={**CFG,'status':'starting','runner_pid':os.getpid(),'errors':[]}
PROCESSES=[]
LOGS=[]
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def save():
 temp=BUILD/'status.tmp'; temp.write_text(json.dumps(STATE,indent=2)+'\n'); temp.replace(BUILD/'status.json')
def request(url,payload=None):
 req=Request(url,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
 with urlopen(req,timeout=45) as response: return json.load(response)
def cli(*args):
 result=subprocess.run(['vss',*args],capture_output=True,text=True)
 if result.returncode: raise RuntimeError('vss exit '+str(result.returncode)+': '+result.stderr.strip())
 return json.loads(result.stdout)
def launch(label,args):
 log=(BUILD/(label+'.log')).open('w'); LOGS.append(log)
 process=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT); PROCESSES.append(process)
 return process
def audit():
 rows=[]
 path=BUILD/'captions.jsonl'
 if path.exists():
  for line in path.read_text().splitlines():
   try: row=json.loads(line)
   except json.JSONDecodeError: continue
   if 'caption' in row: rows.append(row)
 return rows
def summarize():
 rows=audit()
 valid=[r for r in rows if isinstance(r.get('parsed'),dict) and r['parsed'].get('decision') in ('Yes','No')]
 STATE.update(processed_windows=len(valid),positive_windows=sum(r['parsed']['decision']=='Yes' for r in valid),invalid_nonempty_responses=sum(bool(r.get('response')) and r not in valid for r in rows),empty_terminal_or_error_captions=sum(not r.get('response') for r in rows))
 save()
try:
 if (BUILD/'status.json').exists(): raise RuntimeError('This playback already started; no automatic replay is allowed.')
 save()
 request(CFG['origin']+'/alert-bridge/health')
 (BUILD/'rules-before.json').write_text(json.dumps(request(CFG['origin']+'/alert-bridge/api/v1/realtime'),indent=2))
 for source,target in [(str(BUNDLE/'collect-captions.py'),AUDIT_PREFIX+'-collect.py'),(str(BUILD/'detector.json'),AUDIT_PREFIX+'-detector.json')]:
  subprocess.run(['docker','cp',source,'vss-rtvi-vlm:'+target],check=True)
 audit_log=(BUILD/'captions.jsonl').open('w'); LOGS.append(audit_log)
 audit_err=(BUILD/'caption-reader.log').open('w'); LOGS.append(audit_err)
 collector=subprocess.Popen(['docker','exec','-e','PYTHONPATH=/opt/nvidia/rtvi/rtvi','vss-rtvi-vlm','python3','-u',AUDIT_PREFIX+'-collect.py',AUDIT_PREFIX+'-detector.json'],stdout=audit_log,stderr=audit_err); PROCESSES.append(collector)
 deadline=time.monotonic()+20
 while 'consumer_ready' not in (BUILD/'captions.jsonl').read_text():
  if collector.poll() is not None: raise RuntimeError('Caption collector failed; see caption-reader.log')
  if time.monotonic()>deadline: raise RuntimeError('Caption collector did not become ready')
  time.sleep(.2)
 server=launch('mediamtx',[str(args.mediamtx.resolve()),str(BUILD/'mediamtx.yml')])
 time.sleep(.5)
 if server.poll() is not None: raise RuntimeError('MediaMTX startup failed')
 request('http://127.0.0.1:9998/v3/paths/list')
 command=['ffmpeg','-hide_banner','-nostdin','-loglevel','info','-stats_period','10','-progress',str(BUILD/'publisher-progress.txt'),'-re','-i',str(BUILD/'source.mp4'),'-map','0:v:0','-an','-c:v','copy','-rtsp_transport','tcp','-f','rtsp',CFG['source_url']]
 STATE.update(publisher_command=command,feed_started_at=now())
 clock=time.monotonic(); publisher=launch('publisher',command); STATE['publisher_pid']=publisher.pid
 deadline=time.monotonic()+10
 while True:
  if publisher.poll() is not None: raise RuntimeError('Publisher startup failed')
  paths=request('http://127.0.0.1:9998/v3/paths/list')
  if any(p['name']==SOURCE_PATH and p.get('ready') for p in paths['items']): break
  if time.monotonic()>deadline: raise RuntimeError('RTSP source did not become ready')
  time.sleep(.2)
 reader=launch('live-reader',['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-rtsp_transport','tcp','-i',CFG['source_url'],'-map','0:v:0','-an','-vf','fps=1,scale=320:180','-f','framemd5',str(BUILD/'live-frame-hashes.txt')])
 if CFG.get('existing_sensor_id'):
  added={'sensor_id':CFG['existing_sensor_id'],'name':CFG['sensor_name'],'reused':True}
 else:
  added=cli('vios','add',CFG['source_url'],'--name',CFG['sensor_name'],'--type','stream','--raw')
 (BUILD/'sensor-added.json').write_text(json.dumps(added,indent=2)+'\n')
 STATE['sensor_id']=added['sensor_id']
 streams=request(CFG['origin']+'/vst/api/v1/sensor/'+STATE['sensor_id']+'/streams')
 (BUILD/'sensor-streams.json').write_text(json.dumps(streams,indent=2)+'\n')
 main=next((s for s in streams if s.get('isMain')),streams[0] if len(streams)==1 else None)
 if not main or not main['url'].startswith('rtsp://'): raise RuntimeError('Registered sensor has no RTSP stream')
 STATE['stream_id']=main['streamId']
 detector=json.loads((BUILD/'detector.json').read_text())
 payload={**detector['params'],'live_stream_url':CFG['source_url'],'sensor_id':STATE['sensor_id'],'sensor_name':CFG['sensor_name'],'alert_type':detector['alert_type']}
 (BUILD/'rule-request.json').write_text(json.dumps(payload,indent=2)+'\n')
 created=request(CFG['origin']+'/alert-bridge/api/v1/realtime',payload)
 (BUILD/'rule-created.json').write_text(json.dumps(created,indent=2)+'\n')
 STATE['rule_id']=created['id']; STATE['monitoring_started_at']=created['created_at']
 rule=request(CFG['origin']+'/alert-bridge/api/v1/realtime/'+created['id'])
 (BUILD/'rule-active.json').write_text(json.dumps(rule,indent=2)+'\n')
 if rule['rule']['status']!='active': raise RuntimeError('New monitoring rule is not active')
 if time.monotonic()-clock>=CFG.get('preroll_seconds',0): raise RuntimeError('Rule admission missed the blank lead-in; playback coverage is incomplete')
 STATE.update(status='playing_once_with_monitoring',monitoring_ready_at=now(),registration_delay_seconds=round(time.monotonic()-clock,3))
 save(); print(json.dumps({'status':STATE['status'],'sensor':CFG['sensor_name'],'rule_id':STATE['rule_id'],'registration_delay_seconds':STATE['registration_delay_seconds']}),flush=True)
 last=0
 while publisher.poll() is None:
  elapsed=time.monotonic()-clock
  if server.poll() is not None: raise RuntimeError('RTSP server exited early')
  if elapsed>CFG['source_duration_seconds']+40: raise RuntimeError('Publisher exceeded one-pass duration')
  if elapsed-last>=15:
   summarize(); print(json.dumps({'elapsed_seconds':round(elapsed,1),'processed_windows':STATE['processed_windows'],'positive_windows':STATE['positive_windows'],'invalid_responses':STATE['invalid_nonempty_responses']}),flush=True); last=elapsed
  time.sleep(.5)
 STATE.update(publisher_exit_code=publisher.returncode,feed_ended_at=now(),playback_seconds=round(time.monotonic()-clock,3))
 if publisher.returncode!=0 or STATE['playback_seconds']<CFG['source_duration_seconds']-3: raise RuntimeError('One-pass publisher failed or ended early')
 STATE['reader_exit_code']=reader.wait(timeout=15)
 paths=request('http://127.0.0.1:9998/v3/paths/list')
 (BUILD/'paths-after-eof.json').write_text(json.dumps(paths,indent=2)+'\n')
 if any(p['name']==SOURCE_PATH and p.get('ready') for p in paths['items']): raise RuntimeError('Source remained ready after EOF')
 hashes=[line.split(',')[-1].strip() for line in (BUILD/'live-frame-hashes.txt').read_text().splitlines() if line and not line.startswith('#')]
 STATE.update(eof_verified=True,live_sampled_frames=len(hashes),unique_live_frame_hashes=len(set(hashes)),status='draining_inference')
 if len(hashes)<CFG['source_duration_seconds']-10 or len(set(hashes))<2: raise RuntimeError('Independent live reader did not verify moving video through EOF')
 save(); print('One playback reached EOF; waiting for queued inference and indexing.',flush=True)
 # Drain until a terminal caption arrives, with a finite deadline.
 deadline=time.monotonic()+CFG.get('drain_timeout_seconds',240)
 while time.monotonic()<deadline:
  summarize()
  rows=audit()
  indices={int(row['caption']['info']['chunkIdx']) for row in rows if row.get('response')}
  # Native EOS finishes with a normal final caption, not an empty sentinel.
  ends=[datetime.fromisoformat(row['caption']['end'].replace('Z','+00:00')).timestamp() for row in rows if row.get('response')]
  eof=datetime.fromisoformat(STATE['feed_ended_at'].replace('Z','+00:00')).timestamp()
  if indices and indices==set(range(max(indices)+1)) and max(ends)>=eof-1: break
  time.sleep(1)
 else: raise RuntimeError('Inference did not reach its terminal caption before the drain deadline')
 time.sleep(5)
 summarize()
 incidents=request(CFG['origin']+'/alert-bridge/api/v1/realtime/incidents?'+urlencode({'sensor_id':CFG['sensor_name'],'category':DETECTOR['alert_type'],'limit':1000}))
 (BUILD/'incidents.json').write_text(json.dumps(incidents,indent=2)+'\n')
 STATE['indexed_alert_total']=incidents['total']
 if STATE['processed_windows']==0: raise RuntimeError('No valid VLM classifications were received')
 if STATE['invalid_nonempty_responses']: raise RuntimeError('Some VLM responses were not valid explicit-decision JSON')
 (BUILD/'rules-after.json').write_text(json.dumps(request(CFG['origin']+'/alert-bridge/api/v1/realtime'),indent=2))
 STATE.update(status='complete',finished_at=now())
 save(); print(json.dumps({'status':'complete','processed_windows':STATE['processed_windows'],'positive_windows':STATE['positive_windows'],'indexed_alert_total':STATE['indexed_alert_total'],'eof_verified':True}),flush=True)
except BaseException as error:
 STATE['status']='failed'; STATE['errors'].append(str(error)); save(); traceback.print_exc(); raise
finally:
 if (BUILD/'captions.jsonl').exists():
  for line in (BUILD/'captions.jsonl').read_text().splitlines():
   try: ready=json.loads(line)
   except json.JSONDecodeError: continue
   if ready.get('status')=='consumer_ready' and isinstance(ready.get('pid'),int):
    subprocess.run(['docker','exec','vss-rtvi-vlm','kill','-INT',str(ready['pid'])],capture_output=True)
    break
 for process in reversed(PROCESSES):
  if process.poll() is None:
   process.send_signal(signal.SIGINT)
   try: process.wait(timeout=10)
   except subprocess.TimeoutExpired: process.kill(); process.wait()
 for log in LOGS: log.close()
 save()
