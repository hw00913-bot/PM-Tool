"""Synthetic approvals exercise handoff gates; they authorize no real project."""
import argparse,fcntl,hashlib,json,shutil,stat,subprocess,sys,tempfile,unittest,zipfile
from unittest.mock import patch
from pathlib import Path
import test_workflow as workflow
from test_sources import prototype_source_root
sys.path.insert(0,str(workflow.SCRIPTS))
import project_versions as v
import loop as engine

class VersionedPackageTest(unittest.TestCase):
 def test_actual_frozen_package_import_binding_duplicate_and_change_rejection(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);analysis=base/'analysis'
   class Capture(workflow.WorkflowTest):
    def run_script(self,name,*args,**kwargs):
     if name=='loop.py' and args[0]=='invalidate' and not analysis.exists():shutil.copytree(Path(args[1]),analysis)
     return super().run_script(name,*args,**kwargs)
   Capture('test_full_loop_and_incremental_invalidation').test_full_loop_and_incremental_invalidation()
   registry=base/'ledger.json';v.write(registry,{'schema_version':'1.0','project_id':'PROJECT-TEST','versions':{'VERSION-1':{'label':'1.0.0','revision':0,'changes':[],'stages':{}}}})
   v.attach(analysis,registry,'VERSION-1');bound=v.bind(analysis,'analysis')
   def run(script,*args,expected=0):
    p=subprocess.run([sys.executable,'-B',str(script),*map(str,args)],capture_output=True,text=True);self.assertEqual(expected,p.returncode,p.stdout+p.stderr);return p
   project_root=base/'synthetic-business-project'
   run(workflow.SCRIPTS/'package_delivery.py',analysis,'--project-root',project_root,expected=2)
   self.assertFalse(project_root.exists(), 'Missing confirmation must not create a formal requirement snapshot')
   ev=base/'synthetic-review.md';ev.write_text('合成冻结基线确认记录，仅用于版本交接测试。核对需求、场景、时序、接口、功能、页面和验收一致；不代表任何真实业务审批。'*3)
   v.approve(analysis,'analysis','baseline','synthetic-test',ev,'Synthetic baseline confirmation')
   run(workflow.SCRIPTS/'package_delivery.py',analysis,'--project-root',project_root)
   snapshot=project_root/'需求/1.0.0/snapshots'/bound['snapshot_id']
   self.assertTrue(snapshot.is_dir())
   standard_manifest=v.read(snapshot/'delivery-manifest.json')
   standard_package=snapshot/standard_manifest['package_file']
   self.assertTrue(standard_package.is_file())
   self.assertEqual(bound['snapshot_id'],v.read(snapshot/'prototype-handoff.json')['project_version']['snapshot_id'])
   recorded=v.read(registry)['versions']['VERSION-1']['artifacts'][0]
   snapshot_hashes={p.relative_to(snapshot).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.rglob('*') if p.is_file()}
   self.assertEqual(snapshot_hashes,recorded['files'], 'Registered digest set must cover every delivered file')
   with zipfile.ZipFile(standard_package) as archive:
    for item in standard_manifest['files']:
     self.assertEqual(item['sha256'],hashlib.sha256(archive.read(item['path'])).hexdigest())
   index=v.read(project_root/'版本清单.json')
   self.assertEqual('project-version-index@1',index['schema'])
   self.assertEqual('current',index['versions'][0]['snapshots'][0]['status'])
   self.assertNotIn(str(base),json.dumps(index,ensure_ascii=False), 'Public index must not expose workspace, ledger or evidence paths')
   run(workflow.SCRIPTS/'package_delivery.py',analysis,'--project-root',project_root,expected=2)
   self.assertEqual(snapshot_hashes,{p.relative_to(snapshot).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.rglob('*') if p.is_file()}, 'The same confirmation snapshot must never be overwritten')
   # The explicit legacy output remains usable beside the new standard path.
   run(workflow.SCRIPTS/'package_delivery.py',analysis)
   manifest=v.read(analysis/'deliverables/delivery-manifest.json');package=analysis/manifest['package_file']
   proto=prototype_source_root(workflow.SKILL_DIR);project=base/'prototype'
   run(proto/'skill-library/loop-project-scaffolder/scripts/create_project.py',project,'--name','Synthetic versioned prototype')
   importer=project/'tools/import_analysis.py';run(importer,'inspect',standard_package);run(importer,'import',project,package);run(importer,'import',project,package)
   self.assertEqual('VERSION-1',v.reference(project)['version_id'])
   with zipfile.ZipFile(package) as z:self.assertEqual('VERSION-1',json.loads(z.read('prototype-handoff.json'))['project_version']['version_id'])
   run(workflow.SCRIPTS/'loop.py','invalidate',analysis,'--id','UNKNOWN','--reason','Invalid request','--change-id','BAD-CHANGE',expected=2)
   self.assertEqual([],v.read(registry)['versions']['VERSION-1']['changes'])
   # A same-version upstream change revokes even an immutable previous package.
   run(workflow.SCRIPTS/'loop.py','invalidate',analysis,'--id','US-001','--reason','Synthetic upstream change','--change-id','CHANGE-1')
   run(importer,'inspect',package,expected=2)
   run(importer,'inspect',standard_package,expected=2)
   run(workflow.SCRIPTS/'package_delivery.py',analysis,'--project-root',project_root,expected=2)
   self.assertEqual('stale',v.read(project_root/'版本清单.json')['versions'][0]['snapshots'][0]['status'])
   self.assertEqual(snapshot_hashes,{p.relative_to(snapshot).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.rglob('*') if p.is_file()})
   self.assertEqual(1,len(v.read(registry)['versions']['VERSION-1']['changes']))

class S9VersionedCompletionTest(unittest.TestCase):
 """Exercise actual generators against isolated synthetic approvals and receipts."""

 @classmethod
 def setUpClass(cls):
  cls.fixture_temp = tempfile.TemporaryDirectory(prefix='s9-approved-fixture-')
  cls.fixture = Path(cls.fixture_temp.name)/'analysis'
  destination = cls.fixture
  class Capture(workflow.WorkflowTest):
   def run_script(self, name, *args, **kwargs):
    result = super().run_script(name, *args, **kwargs)
    if name == 'package_delivery.py' and result.returncode == 0 and not destination.exists():
     shutil.copytree(Path(args[0]), destination, ignore=shutil.ignore_patterns('deliverables', '__pycache__'))
    return result
  Capture('test_full_loop_and_incremental_invalidation').test_full_loop_and_incremental_invalidation()
  assert not (cls.fixture/'deliverables').exists()
  assert v.read(cls.fixture/'runtime/loop-state.json')['stages']['S9']['status'] == 'in_progress'

 @classmethod
 def tearDownClass(cls):
  cls.fixture_temp.cleanup()

 def setUp(self):
  self.temp = tempfile.TemporaryDirectory(prefix='s9-completion-')
  self.addCleanup(self.temp.cleanup)
  self.base = Path(self.temp.name)
  self.root = self.base/'analysis'
  shutil.copytree(self.fixture, self.root)
  self.registry = self.base/'ledger.json'
  v.write(self.registry, {'schema_version':'1.0', 'project_id':'PROJECT-S9-SYNTHETIC',
                         'versions':{'VERSION-1':{'label':'1.0.0', 'revision':0, 'changes':[], 'stages':{}}}})
  v.attach(self.root, self.registry, 'VERSION-1')
  self.bound = v.bind(self.root, 'analysis')
  self.evidence = self.base/'synthetic-approval.md'
  self.evidence.write_text('合成 S9 测试审批：只验证本任务隔离工作区、需求包与版本回执关联，不代表真实业务确认或实施批准。'*3)
  v.approve(self.root, 'analysis', 'baseline', 'synthetic-test', self.evidence, 'Synthetic S9 approval')
  self.project = self.base/'synthetic-project'
  self.snapshot = self.project/'需求/1.0.0/snapshots'/self.bound['snapshot_id']
  if not self._testMethodName.startswith('test_legacy_'):
   self.run_script('package_delivery.py', self.root, '--project-root', self.project)

 def run_script(self, name, *args, expected=0):
  p = subprocess.run([sys.executable, '-B', str(workflow.SCRIPTS/name), *map(str,args)], capture_output=True, text=True)
  self.assertEqual(expected, p.returncode, p.stdout+p.stderr)
  return p

 def change_record(self, mutation):
  data = v.read(self.registry)
  mutation(data['versions']['VERSION-1'])
  v.write(self.registry, data)

 def complete(self, expected=0):
  state_path = self.root/'runtime/loop-state.json'
  state_before = state_path.read_bytes()
  ledger_before = self.registry.read_bytes()
  snapshot_before = {p.relative_to(self.snapshot).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in self.snapshot.rglob('*') if p.is_file()}
  result = self.run_script('loop.py', 'complete', self.root, expected=expected)
  self.assertEqual(ledger_before, self.registry.read_bytes(), 'S9 must not write the ledger')
  self.assertEqual(snapshot_before, {p.relative_to(self.snapshot).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in self.snapshot.rglob('*') if p.is_file()}, 'S9 must not repair or overwrite a snapshot')
  if expected:
   self.assertEqual(state_before, state_path.read_bytes(), 'Rejected completion must preserve workflow state')
  else:
   state = v.read(state_path)
   self.assertEqual('complete', state['run']['status'])
   expected_manifest = self.snapshot/'delivery-manifest.json' if self.snapshot.exists() else self.root/'deliverables/delivery-manifest.json'
   self.assertEqual([str(expected_manifest.resolve())], state['stages']['S9']['evidence'])
  return result

 def test_standard_only_completes_and_records_actual_manifest(self):
  self.assertFalse((self.root/'deliverables').exists())
  self.complete()
  self.assertFalse((self.root/'deliverables').exists(), 'S9 must not create a compatibility export')
  manifest=v.read(self.snapshot/'delivery-manifest.json')
  consumer=prototype_source_root(workflow.SKILL_DIR)/'orchestrator/scripts/import_analysis.py'
  result=subprocess.run([sys.executable,'-B',str(consumer),'inspect',str(self.snapshot/manifest['package_file'])],capture_output=True,text=True)
  self.assertEqual(0,result.returncode,result.stdout+result.stderr)

 def test_current_standard_ignores_old_legacy_output(self):
  old = self.root/'deliverables';old.mkdir()
  (old/'delivery-manifest.json').write_text('{obsolete legacy output')
  self.complete()

 def test_corrupt_standard_never_falls_back_to_valid_legacy(self):
  self.run_script('package_delivery.py', self.root)
  package = self.snapshot/v.read(self.snapshot/'delivery-manifest.json')['package_file']
  package.chmod(0o644);package.write_bytes(b'corrupt standard package')
  self.complete(expected=2)

 def test_standard_without_registration_rejects_legacy_fallback(self):
  self.run_script('package_delivery.py', self.root)
  self.change_record(lambda version:version.update(artifacts=[]))
  self.assertIn('缺少交付登记', self.complete(expected=2).stderr)

 def test_duplicate_current_registration_is_rejected(self):
  self.change_record(lambda version:version['artifacts'].append(dict(version['artifacts'][0])))
  self.complete(expected=2)

 def test_wrong_record_revision_is_rejected(self):
  self.run_script('package_delivery.py', self.root)
  self.change_record(lambda version:version['artifacts'][0].update(revision=99))
  self.complete(expected=2)

 def test_wrong_record_kind_is_rejected(self):
  self.run_script('package_delivery.py', self.root)
  self.change_record(lambda version:version['artifacts'][0].update(kind='product-handoff'))
  self.complete(expected=2)

 def test_wrong_record_destination_is_rejected(self):
  self.change_record(lambda version:version['artifacts'][0].update(destination=str(self.base/'other-output')))
  self.complete(expected=2)

 def test_wrong_registered_entrypoint_is_rejected(self):
  self.change_record(lambda version:version['artifacts'][0].update(entrypoint='prototype-handoff.json'))
  self.complete(expected=2)

 def test_extra_snapshot_file_is_rejected(self):
  self.snapshot.chmod(0o755);(self.snapshot/'unexpected.txt').write_text('unregistered')
  self.complete(expected=2)

 def test_missing_snapshot_file_is_rejected(self):
  self.snapshot.chmod(0o755);(self.snapshot/'prototype-handoff.json').unlink()
  self.complete(expected=2)

 def test_symlink_snapshot_file_is_rejected(self):
  self.snapshot.chmod(0o755)
  handoff = self.snapshot/'prototype-handoff.json'
  target = self.base/'linked-handoff.json';target.write_bytes(handoff.read_bytes())
  handoff.unlink();handoff.symlink_to(target)
  self.complete(expected=2)

 def test_copied_workspace_is_not_active_owner(self):
  other = self.base/'other-analysis';shutil.copytree(self.root,other)
  v.bind(other,'analysis');v.approve(other,'analysis','baseline','synthetic-test',self.evidence,'Synthetic other owner')
  self.complete(expected=2)

 def test_wrong_project_identity_is_rejected(self):
  path = self.root/'project-version.json';ref = v.read(path);ref['project_id']='WRONG-PROJECT';v.write(path,ref)
  self.complete(expected=2)

 def test_changed_approved_fact_is_rejected(self):
  path = self.root/'memory/project-facts.json'
  data = v.read(path);data['project']['objective']='changed after approval';v.write(path,data)
  self.complete(expected=2)

 def test_changed_approval_evidence_is_rejected(self):
  self.evidence.write_text('changed synthetic approval evidence'*3)
  self.complete(expected=2)

 def test_revoked_current_receipt_is_rejected(self):
  v.register_change(self.root,'SYNTHETIC-CHANGE-1','analysis','Synthetic upstream change','test',['US-001'])
  self.complete(expected=2)

 def test_g3_status_cannot_be_bypassed_by_current_ledger_approval(self):
  path=self.root/'runtime/loop-state.json';state=v.read(path);state['gates']['G3']['status']='pending';v.write(path,state)
  self.complete(expected=2)

 def test_legacy_bound_current_package_remains_compatible(self):
  self.run_script('package_delivery.py', self.root)
  self.complete()

 def test_legacy_unbound_package_remains_compatible(self):
  (self.root/'project-version.json').unlink()
  self.run_script('package_delivery.py', self.root)
  self.complete()

 def test_legacy_old_version_reference_is_rejected(self):
  self.run_script('package_delivery.py', self.root)
  path=self.root/'deliverables/prototype-handoff.json';handoff=v.read(path)
  handoff['project_version']['snapshot_id']='OLD-SNAPSHOT';v.write(path,handoff)
  self.complete(expected=2)

 def test_legacy_zip_content_change_is_rejected(self):
  self.run_script('package_delivery.py', self.root)
  manifest=v.read(self.root/'deliverables/delivery-manifest.json');package=self.root/manifest['package_file']
  with zipfile.ZipFile(package) as archive:members={name:archive.read(name)for name in archive.namelist()}
  members['memory/project-facts.json']=b'changed package content'
  with zipfile.ZipFile(package,'w') as archive:
   for name,raw in members.items():archive.writestr(name,raw)
  self.complete(expected=2)

 def test_legacy_zip_manifest_must_match_outer_manifest(self):
  self.run_script('package_delivery.py', self.root)
  manifest=v.read(self.root/'deliverables/delivery-manifest.json');package=self.root/manifest['package_file']
  with zipfile.ZipFile(package)as archive:members={name:archive.read(name)for name in archive.namelist()}
  inside=json.loads(members['delivery-manifest.json']);inside['frozen_at']='changed inside ZIP'
  members['delivery-manifest.json']=json.dumps(inside).encode()
  with zipfile.ZipFile(package,'w')as archive:
   for name,raw in members.items():archive.writestr(name,raw)
  self.assertIn('内外交付清单不一致',self.complete(expected=2).stderr)

 def test_legacy_zip_symlink_member_is_rejected(self):
  self.run_script('package_delivery.py', self.root)
  manifest=v.read(self.root/'deliverables/delivery-manifest.json');package=self.root/manifest['package_file']
  with zipfile.ZipFile(package)as archive:members=[(item,archive.read(item.filename))for item in archive.infolist()]
  with zipfile.ZipFile(package,'w')as archive:
   for item,raw in members:
    if item.filename=='memory/project-facts.json':
     item.create_system=3;item.external_attr=(stat.S_IFLNK|0o777)<<16
    archive.writestr(item,raw)
  self.assertIn('符号链接成员',self.complete(expected=2).stderr)

 def test_invalid_existing_version_marker_never_downgrades_to_legacy(self):
  self.run_script('package_delivery.py', self.root)
  marker=self.root/'project-version.json';original=marker.read_bytes()
  for value in [b'{}',b'null',b'[]',b'',b'{bad',b'{"schema_version":"1.0"}',
                b'{"schema_version":"unknown","project_id":"P","version_id":"V","registry":"/none"}']:
   with self.subTest(marker=value):
    marker.write_bytes(value)
    self.complete(expected=2)
  marker.write_bytes(original)

 def test_symlink_version_marker_is_rejected(self):
  marker=self.root/'project-version.json';target=self.base/'linked-version.json'
  target.write_bytes(marker.read_bytes());marker.unlink();marker.symlink_to(target)
  self.complete(expected=2)

 def test_cross_loop_registered_artifact_corruption_never_downgrades(self):
  import os
  dev = Path(os.environ.get('DEVELOPMENT_SOURCE_ROOT', Path.home()/'.codex/skills/development-planning-loop'))
  sys.path.insert(0, str(dev/'scripts'))
  from dpl.adapters import import_product
  from dpl.common import PlanningError
  package = self.snapshot/v.read(self.snapshot/'delivery-manifest.json')['package_file']
  source = self.base/'unpacked'
  with zipfile.ZipFile(package) as archive: archive.extractall(source)
  importer = prototype_source_root(workflow.SKILL_DIR)/'orchestrator/scripts/import_analysis.py'
  def proto():
   return subprocess.run([sys.executable,'-B',str(importer),'inspect',str(package)],capture_output=True,text=True)
  self.assertEqual('registered-delivery',json.loads(proto().stdout)['approvalProvenance'])
  self.assertEqual('registered-delivery',import_product(source,'analysis')['analysis_provenance'])
  original = v.read(self.registry)
  for key,value in [('kind','product-handoff'),('stage','product')]:
   with self.subTest(key=key):
    data = v.read(self.registry)
    data['versions']['VERSION-1']['artifacts'][0][key] = value
    v.write(self.registry,data)
    self.assertNotEqual(0,proto().returncode)
    with self.assertRaises(PlanningError): import_product(source,'analysis')
    v.write(self.registry,original)
  data = v.read(self.registry);data['versions']['VERSION-1']['artifacts']=[];v.write(self.registry,data)
  self.assertEqual('owner-state-only',json.loads(proto().stdout)['approvalProvenance'])
  self.assertEqual('owner-state-only',import_product(source,'analysis')['analysis_provenance'])

 def test_wrong_version_identity_is_rejected(self):
  marker=self.root/'project-version.json';ref=v.read(marker);ref['version_id']='UNKNOWN-VERSION';v.write(marker,ref)
  self.complete(expected=2)

 def test_wrong_marker_snapshot_is_rejected(self):
  marker=self.root/'project-version.json';ref=v.read(marker);ref['snapshot_id']='OLD-SNAPSHOT';v.write(marker,ref)
  self.complete(expected=2)

 def test_legacy_manifest_protocol_and_engine_are_checked(self):
  for key,value in [('schema_version','unknown'),('analysis_engine',{'name':'wrong-engine','version':'0'})]:
   with self.subTest(field=key):
    self.run_script('package_delivery.py', self.root)
    path=self.root/'deliverables/delivery-manifest.json';manifest=v.read(path);package=self.root/manifest['package_file']
    with zipfile.ZipFile(package)as archive:members={name:archive.read(name)for name in archive.namelist()}
    manifest[key]=value;v.write(path,manifest);members['delivery-manifest.json']=path.read_bytes()
    with zipfile.ZipFile(package,'w')as archive:
     for name,raw in members.items():archive.writestr(name,raw)
    self.complete(expected=2)

 def test_active_ledger_writer_blocks_completion_without_state_write(self):
  lock=self.registry.with_suffix(self.registry.suffix+'.lock')
  with lock.open('rb')as handle:
   fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
   try:self.assertIn('正在更新',self.complete(expected=2).stderr)
   finally:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

 def test_missing_existing_ledger_lock_is_not_created(self):
  lock=self.registry.with_suffix(self.registry.suffix+'.lock');lock.unlink()
  self.complete(expected=2)
  self.assertFalse(lock.exists(),'Completion must not create a missing ledger lock')

 def complete_with_concurrent_change(self, mutation):
  original_read=zipfile.ZipFile.read;changed={}
  paths=[self.registry,self.root/'project-version.json',self.root/'runtime/loop-state.json',self.root/'runtime/verification-report.json']
  def read_then_mutate(archive,name,*args,**kwargs):
   raw=original_read(archive,name,*args,**kwargs)
   if name=='delivery-manifest.json' and not changed:
    mutation()
    changed.update({str(p):p.read_bytes()for p in paths})
   return raw
  args=argparse.Namespace(project_dir=str(self.root),stage=None,evidence=None)
  with patch.object(zipfile.ZipFile,'read',read_then_mutate):
   with self.assertRaises(engine.LoopError):engine.cmd_complete(args)
  self.assertTrue(changed,'Concurrent mutation must run during ZIP validation')
  for p in paths:self.assertEqual(changed[str(p)],p.read_bytes(),'Rejected completion must not overwrite concurrently changed state')

 def test_concurrent_approval_revocation_is_rechecked(self):
  def mutation():
   data=v.read(self.registry);data['versions']['VERSION-1']['stages']['analysis']['approvals']={};v.write(self.registry,data)
  self.complete_with_concurrent_change(mutation)

 def test_concurrent_owner_change_is_rechecked(self):
  def mutation():
   data=v.read(self.registry);data['versions']['VERSION-1']['stages']['analysis']['root']=str(self.base/'other-owner');v.write(self.registry,data)
  self.complete_with_concurrent_change(mutation)

 def test_concurrent_marker_change_is_rechecked(self):
  def mutation():
   marker=self.root/'project-version.json';ref=v.read(marker);ref['project_id']='CONCURRENT-PROJECT';v.write(marker,ref)
  self.complete_with_concurrent_change(mutation)

 def test_concurrent_g3_change_preserves_changed_state(self):
  def mutation():
   path=self.root/'runtime/loop-state.json';state=v.read(path);state['gates']['G3']['status']='pending';v.write(path,state)
  self.complete_with_concurrent_change(mutation)

 def test_concurrent_verification_change_is_rechecked(self):
  def mutation():
   path=self.root/'runtime/verification-report.json';report=v.read(path);report['status']='fail';v.write(path,report)
  self.complete_with_concurrent_change(mutation)

if __name__=='__main__':unittest.main()
