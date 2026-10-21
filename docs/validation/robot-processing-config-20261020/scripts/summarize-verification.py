"""Reconcile later targeted reruns without deleting the original test evidence."""
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
import json

root=Path(__file__).resolve().parent
if not (root/'backend-final.xml').exists():
 root=root.parent
sets={
 'backend':['backend-final.xml','legacy-temporal-final.xml','parallel-final.xml','runtime-worker-final.xml','backend-dependency-final.xml','migration-cli-final.xml','visual-task-guard-final.xml','ready-guards-final.xml','capture-package-final.xml','capture-package-temporal-final.xml'],
 'frontend':['frontend-final.xml','frontend-gap-final.xml','frontend-dependency-final.xml'],
 'robot':['robot-supported-final.xml'],
}
summary={}
for family,files in sets.items():
 cases={};sources={}
 for file in files:
  occurrences=Counter()
  for c in ET.parse(root/file).getroot().iter('testcase'):
   identity=(c.attrib.get('classname',''),c.attrib['name'])
   occurrences[identity]+=1
   key=(*identity,occurrences[identity])
   cases[key]=c;sources[key]=file
 totals=dict(tests=len(cases),failures=0,errors=0,skipped=0,passed=0)
 suite=ET.Element('testsuite',name=family+' latest result per test and parameter occurrence')
 for key,c in sorted(cases.items()):
  tag=next((x.tag for x in c if x.tag in ('failure','error','skipped')),None)
  totals[{'failure':'failures','error':'errors','skipped':'skipped'}.get(tag,'passed')]+=1
  props=c.find('properties')
  if props is None:props=ET.SubElement(c,'properties')
  ET.SubElement(props,'property',name='evidence_source',value=sources[key])
  suite.append(c)
 for k,v in totals.items():suite.set(k,str(v))
 suite.set('time',str(round(sum(float(c.attrib.get('time',0)) for c in cases.values()),3)))
 ET.ElementTree(suite).write(root/(family+'-reconciled-final.xml'),encoding='utf-8',xml_declaration=True)
 summary[family]={**totals,'sources':files}
(root/'verification-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
