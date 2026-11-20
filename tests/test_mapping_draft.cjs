"use strict";
const assert=require("node:assert/strict"),mapping=require("../web/comparison-mapping.js");
let drafts;try{drafts=require("../web/mapping-draft.js")}catch(e){if(e.code!=="MODULE_NOT_FOUND")throw e}
assert.equal(typeof drafts?.create,"function","browser edits need an independent draft");
const f=path=>({path,doc:"Поле "+path}),before=["old","old/a","old/sub","old/sub/value"].map(f),after=["new","new/a","new/sub","new/sub/value","other","other/a","other/sub","other/sub/value","target"].map(f);
const parent={beforePath:"old",afterPath:"new",comment:""},snapshot=JSON.stringify({before,after,parent});
const create=entries=>drafts.create({before,after,savedPairs:[parent],entries:entries||[{...parent,origin:"confirmed",selected:true}],suggest:mapping.suggestDescendants});
const deletedOnly=drafts.create({before,after,deleted:{before:["old/sub"],after:[]}});
for(const parentFirst of [true,false]){
 const preview=drafts.create({before,after,entries:[],suggest:mapping.suggestDescendants});
 const child={beforePath:"old/a",afterPath:"new/a"};
 const id=preview.add({...child,comment:"Вручную"});
 if(parentFirst)preview.add(parent);
 preview.remove(id);
 if(!parentFirst)preview.add(parent);
 assert.ok(!preview.rows.some(row=>row.beforePath===child.beforePath&&row.afterPath===child.afterPath),
  "a removed manual pair must not come back as an automatically selected child");
 assert.deepEqual(preview.dismissedPairs,[child]);
 preview.add({...child,comment:"Возвращено вручную"});
 assert.deepEqual(preview.dismissedPairs,[]);
}
assert.equal(deletedOnly.summary().dirty,true,"standalone exclusions can be applied without a saved parent");
assert.equal(deletedOnly.hasEdits(),true,"workbook exclusions change the draft");
const excludeBlock=mapping.suggestDescendants([parent],before,after,{before:["old/sub"],after:[]});
assert.deepEqual(excludeBlock.suggestions.map(p=>p.beforePath),["old/a"],"deleting a whole block excludes automatic matches for its descendants");
const draft=create();assert.equal(draft.rows.length,4);assert.equal(draft.summary().dirty,true,"proposed descendants are not saved yet");
assert.equal(draft.hasEdits(),false,"automatic suggestions on first open do not count as user edits");
{
 const preview=create(),child=preview.rows.find(row=>row.origin==="relative"&&row.beforePath==="old/a");
 preview.toggle(child.id,false);
 assert.deepEqual(preview.dismissedPairs,[{beforePath:child.beforePath,afterPath:child.afterPath}],"unchecking an individual child survives application");
 preview.toggle(child.id,true);
 assert.deepEqual(preview.dismissedPairs,[],"rechecking a child withdraws the dismissal");
 assert.ok(!preview.automaticPairs.some(pair=>pair.beforePath===child.beforePath),"rechecked children are independent of suggestions");
 assert.ok(preview.automaticPairs.every(pair=>pair.parentBeforePath===parent.beforePath&&pair.parentAfterPath===parent.afterPath));
 const accepted=preview.adoptedPairs.find(pair=>pair.beforePath===child.beforePath);
 preview.selectProposals(false);
 preview.remove(preview.rows.find(row=>row.origin==="confirmed").id);
 assert.deepEqual(preview.summary().pairs,[accepted],"an individually accepted child survives bulk selection and parent removal");
 preview.toggle(child.id,false);
 assert.deepEqual(preview.summary().pairs,[],"an individual acceptance can be withdrawn");
 assert.deepEqual(preview.dismissedPairs,[parent,child].map(({beforePath,afterPath})=>({beforePath,afterPath})));
}
{
 const preview=create();
 const block=preview.rows.find(row=>row.beforePath==="old/sub");
 preview.toggle(block.id,false);
 preview.toggle(block.id,true);
 preview.remove(preview.rows.find(row=>row.origin==="confirmed").id);
 assert.deepEqual(preview.summary().pairs,preview.adoptedPairs,"accepting a proposed block preserves that pair alone");
 assert.equal(preview.adoptedPairs.length,1);
 const edited={beforePath:block.beforePath,afterPath:block.afterPath,comment:"Проверено"};
 preview.edit(block.id,edited);
 assert.deepEqual(preview.summary().pairs,[edited],"editing an adopted block's comment does not select its descendants");
}
const unconfirmed=create([{...parent,origin:"workbook",selected:true,decision:"Не проверено"}]);
assert.deepEqual(unconfirmed.summary().pairs,[parent],"unconfirmed Excel parents must not select their descendants");
assert.equal(unconfirmed.rows.length,1,"unconfirmed Excel parents propose only themselves");
unconfirmed.edit(unconfirmed.rows[0].id,parent);assert.equal(unconfirmed.rows.length,4,"explicit browser confirmation can propose descendants");
draft.selectProposals(false);assert.deepEqual(draft.summary().pairs,[parent]);assert.equal(draft.summary().dirty,false);
assert.equal(draft.hasEdits(),true,"deselecting proposals is still a draft edit");
{
 const untouched=create();
 const choice=untouched.rows.find(row=>row.origin==="relative").id;
 untouched.toggle(choice,false);
 untouched.toggle(choice,true);
 assert.equal(untouched.hasEdits(),true,"individually accepting a suggestion records a draft edit");
 const rejected={beforePath:"old/a",afterPath:"new/a"};
 for(const [decision,selected] of [["Требует уточнения",false],["Не проверено",true]]){
  const proposal={...rejected,origin:"workbook",decision,selected};
  const draft=create([parent,proposal]);
  assert.equal(draft.hasEdits(),false,"an untouched Excel row is not a user edit");
  assert.deepEqual(draft.dismissedPairs,[]);
  draft.selectProposals(false);
  assert.deepEqual(draft.dismissedPairs,[],"bulk deselection alone does not reject individual pairs");
  draft.remove(draft.rows.find(row=>row.origin==="workbook").id);
  assert.deepEqual(draft.dismissedPairs,[rejected],decision);
  assert.ok(!draft.rows.some(row=>row.beforePath==="old/a"&&row.afterPath==="new/a"));
  assert.equal(draft.summary().dirty,true,"the exact exclusion must be applied even if the row was already unchecked");
 }
 const alternative={beforePath:"old/a",afterPath:"target",origin:"workbook",selected:false};
 const draft=create([parent,alternative]);
 draft.remove(draft.rows.find(row=>row.origin==="workbook").id);
 assert.deepEqual(draft.dismissedPairs,[{beforePath:"old/a",afterPath:"target"}]);
 assert.ok(draft.rows.some(row=>row.beforePath==="old/a"&&row.afterPath==="new/a"&&row.selected),"another target with the same source retains its own proposal");
}
const parentId=draft.rows.find(r=>r.origin==="confirmed").id;
{
 const changed=create();
 changed.edit(changed.rows[0].id,{...parent,comment:"Согласовано"});
 const extra={beforePath:"old/a",afterPath:"target",comment:"Вручную"};
 changed.add(extra);
 changed.selectProposals(false);
 assert.deepEqual(changed.summary().pairs,[{...parent,comment:"Согласовано"},extra],"bulk removal preserves edited and new explicit browser pairs");
 assert.ok(changed.rows.some(row=>row.origin==="relative"&&!row.selected));
}
draft.edit(parentId,{beforePath:"old",afterPath:"other",comment:"Исправлено"});
assert.ok(draft.rows.filter(r=>r.origin==="relative").every(r=>r.afterPath.startsWith("other/")));assert.equal(draft.summary().dirty,true);
const manual=draft.add({beforePath:"old/a",afterPath:"target",comment:"Проверено"});assert.ok(!draft.rows.some(r=>r.origin==="relative"&&r.beforePath==="old/a"));
assert.throws(()=>draft.add({beforePath:"old/a",afterPath:"target",comment:"Повтор"}),/уже|дубликат/);
const valid=JSON.stringify(draft.rows);assert.throws(()=>draft.edit(manual,{beforePath:"old/a",afterPath:"missing",comment:""}),/путь|схем/);assert.equal(JSON.stringify(draft.rows),valid,"bad edit must not lose the previous choice");
draft.remove(manual);assert.ok(!draft.rows.some(r=>r.beforePath==="old/a"&&r.afterPath==="target"),"removed link must not return as an automatic suggestion");
draft.toggle(parentId,false);assert.ok(!draft.rows.some(r=>r.origin==="relative"));assert.equal(draft.summary().pairs.length,0);
draft.toggle(parentId,true);assert.ok(draft.rows.some(r=>r.origin==="relative"));draft.selectProposals(true);assert.ok(draft.rows.filter(r=>r.origin!=="confirmed").every(r=>r.selected));
assert.equal(JSON.stringify({before,after,parent}),snapshot,"schemas and saved pairs are immutable");
const exact=create([{...parent,origin:"confirmed",selected:true}]);exact.selectProposals(false);assert.equal(exact.summary().dirty,false);exact.edit(exact.rows[0].id,{...parent,comment:"Новый комментарий"});assert.equal(exact.summary().dirty,true);
assert.deepEqual(exact.dismissedPairs,[],"editing only a comment cannot dismiss the existing pair");
const first=exact.add({beforePath:"old/a",afterPath:"new/a",comment:""});exact.add({beforePath:"old/a",afterPath:"target",comment:"Разделение"});assert.ok(exact.summary().pairs.filter(p=>p.beforePath==="old/a").length===2);
exact.toggle(first,false);assert.ok(exact.summary().pairs.every(p=>!(p.beforePath==="old/a"&&p.afterPath==="new/a")));
const moved=create(),autoA=moved.rows.find(r=>r.origin==="relative"&&r.beforePath==="old/a");
moved.edit(autoA.id,{beforePath:"old/sub",afterPath:"target",comment:"Ручная замена"});
const oldPath={beforePath:autoA.beforePath,afterPath:autoA.afterPath};
assert.deepEqual(moved.dismissedPairs,[oldPath],"moving both endpoints rejects the original proposal");
assert.ok(!moved.rows.some(r=>r.beforePath===oldPath.beforePath&&r.afterPath===oldPath.afterPath),"the moved pair cannot be proposed again");
moved.selectProposals(true);
assert.ok(!moved.summary().pairs.some(r=>r.beforePath===oldPath.beforePath),"bulk selection cannot restore a moved pair");
const restored=moved.add({...oldPath,comment:"Возвращено вручную"});
assert.notEqual(restored,autoA.id,"explicitly restoring the original pair gets a distinct row identity");
assert.deepEqual(moved.dismissedPairs,[],"explicit re-add withdraws the dismissal");
assert.equal(new Set(moved.rows.map(r=>r.id)).size,moved.rows.length);
moved.toggle(restored,false);assert.equal(moved.rows.find(r=>r.id===autoA.id).selected,true,"unchecking restored row must not deselect the moved pair");
moved.remove(restored);assert.ok(moved.rows.some(r=>r.id===autoA.id&&r.afterPath==="target"));
const shifted=create([{...parent,origin:"confirmed"},{...oldPath,origin:"confirmed"}]);
shifted.edit(shifted.rows.find(row=>row.beforePath===oldPath.beforePath).id,{beforePath:"old/sub",afterPath:oldPath.afterPath});
assert.ok(!shifted.conflicts.some(row=>row.beforePath===oldPath.beforePath&&row.afterPath===oldPath.afterPath),"the deliberately moved pair no longer needs a decision");
const empty=drafts.create({before,after,savedPairs:[],entries:[],suggest:mapping.suggestDescendants});assert.equal(empty.summary().dirty,false);const added=empty.add(parent);assert.equal(empty.summary().dirty,true);empty.remove(added);assert.equal(empty.summary().dirty,false);
const limit='я'.repeat(32767),tooLong=limit+'ю';
const longDraft=create();
const longId=longDraft.add({beforePath:"old/a",afterPath:"target",comment:limit});
assert.throws(()=>longDraft.edit(longId,{beforePath:"old/a",afterPath:"target",comment:tooLong}),/32767/);
assert.equal(longDraft.rows.find(r=>r.id===longId).comment,limit);
assert.throws(()=>longDraft.add({beforePath:"old/sub",afterPath:"other/sub",comment:tooLong}),/32767/);
const legacy=create([{...parent,comment:tooLong}]);
const legacyRow=legacy.rows[0];
assert.equal(legacyRow.comment,tooLong);
assert.match(legacy.summary().errors.join(' '),/old → new.*32767/);
legacy.edit(legacyRow.id,{...parent,comment:limit});
assert.deepEqual(legacy.summary().errors,[]);
console.log("Mapping draft: edit/add/remove, source validation, duplicates, split, parent regeneration, selection, dirty state and immutable saved data passed.");
