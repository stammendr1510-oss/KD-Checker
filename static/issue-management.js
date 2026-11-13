// Engineering decisions are separate from the original automated findings.
const findingStatuses={new:'Новое',in_progress:'В работе',resolved:'Устранено',rejected:'Отклонено с обоснованием'};
function findingDate(value){return value?new Date(value).toLocaleString('ru-RU'):'—';}
function findingEditor(state,runId){
 if(!state)return 'Управление недоступно';
 const previous=state.previous;
 const prior=previous?`<p class="finding-prior">Сопоставлено с проверкой №${esc(previous.run_id)}: ${esc(findingStatuses[previous.status])}. Комментарий: ${esc(previous.comment||'—')}.<br>Решение для текущей проверки сохраните отдельно.</p>`:
   (state.match_kind==='ambiguous'?'<p class="finding-prior">Неоднозначное совпадение: требуется ручная сверка.</p>':'');
 return `<div class="finding-editor" data-finding-id="${state.id}" data-run-id="${runId}" data-version="${state.version}">
  ${prior}<label>Статус<select aria-label="Статус замечания">${Object.entries(findingStatuses).map(([key,label])=>`<option value="${key}" ${state.status===key?'selected':''}>${esc(label)}</option>`).join('')}</select></label>
  <label>Комментарий инженера<textarea maxlength="4000" aria-label="Комментарий инженера">${esc(state.comment)}</textarea></label>
  <p class="finding-updated">Дата изменения: ${esc(findingDate(state.updated_at))}</p>
  <button type="button" data-save-finding>Сохранить решение</button><button type="button" data-finding-history>История изменений</button>
  <div class="finding-message" role="status" aria-live="polite"></div><div class="finding-audit hidden"></div></div>`;
}

document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-save-finding],[data-finding-history]');
 if(!button)return;
 const editor=button.closest('.finding-editor');
 const message=editor.querySelector('.finding-message');
 const url=`/api/runs/${editor.dataset.runId}/findings/${editor.dataset.findingId}`;
 button.disabled=true;
 try{
  if(button.hasAttribute('data-save-finding')){
   const status=editor.querySelector('select').value;
   const comment=editor.querySelector('textarea').value;
   if(status==='rejected'&&!comment.trim())throw Error('Для отклонения замечания укажите обоснование');
   const result=await api(url,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status,comment,expected_version:Number(editor.dataset.version)})});
   editor.dataset.version=result.version;
   editor.querySelector('.finding-updated').textContent='Дата изменения: '+findingDate(result.updated_at);
   editor.querySelector('.finding-audit').classList.add('hidden');
   message.textContent='Решение сохранено. Результат инженерной проверки не изменён.';
  }else{
   const events=await api(url+'/history');
   const audit=editor.querySelector('.finding-audit');
   audit.textContent=events.length?events.map(item=>`${findingDate(item.changed_at)}\nСтатус: ${findingStatuses[item.old_status]} → ${findingStatuses[item.new_status]}\nКомментарий до: ${item.old_comment||'—'}\nКомментарий после: ${item.new_comment||'—'}`).join('\n\n'):'Изменений ещё нет';
   audit.classList.remove('hidden');
   message.textContent='';
  }
 }catch(error){message.textContent=error.message;}
 finally{button.disabled=false;}
});
