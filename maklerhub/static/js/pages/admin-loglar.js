const L={ok:'OK',warn:'DİQQƏT',err:'XƏTA',info:'MƏLUMAT'};
let D=[];               // serverdən gələn loqlar: [vaxt, səviyyə, mənbə, mesaj, detallar]
let lv='all',src='all',q='',page=0,PER=8;
const tb=document.getElementById('tb');
function view(){return D.filter(r=>(lv==='all'||r[1]===lv)&&(src==='all'||r[2]===src)&&(!q||(r[3]+r[2]+r[4]).toLowerCase().includes(q)));}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function render(){
  const v=view(),pages=Math.max(1,Math.ceil(v.length/PER));
  if(page>=pages)page=pages-1;
  const s=v.slice(page*PER,page*PER+PER);
  tb.innerHTML=s.map(r=>`<tr><td class="t">${r[0]}</td><td><span class="lv ${r[1]}">${L[r[1]]}</span></td><td class="srcn">${esc(r[2])}</td><td>${esc(r[3])}<div class="det">${esc(r[4]).replace(/\n/g,'<br>')}</div></td></tr>`).join('');
  tb.querySelectorAll('tr').forEach(tr=>tr.addEventListener('click',()=>tr.classList.toggle('open')));
  document.getElementById('empty').style.display=v.length?'none':'block';
  document.getElementById('pinfo').textContent=v.length?`${page*PER+1}–${page*PER+s.length} / ${v.length} qeyd`:'0 qeyd';
  document.getElementById('prev').disabled=page===0;
  document.getElementById('next').disabled=page>=pages-1;
}
function kpi(k){
  document.getElementById('k-all').textContent=k.all.toLocaleString('en-US');
  document.getElementById('k-ok').textContent=k.ok.toLocaleString('en-US');
  document.getElementById('k-warn').textContent=k.warn.toLocaleString('en-US');
  document.getElementById('k-err').textContent=k.err.toLocaleString('en-US');
}
async function load(){
  const j=await MH.api('/admin/api/logs');
  D=j.rows;kpi(j.kpi);render();
}
document.querySelectorAll('#lvf .fchip').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('#lvf .fchip').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');lv=b.dataset.lv;page=0;render();
}));
document.getElementById('srcf').addEventListener('change',e=>{src=e.target.value;page=0;render();});
document.getElementById('q').addEventListener('input',e=>{q=e.target.value.trim().toLowerCase();page=0;render();});
document.getElementById('prev').addEventListener('click',()=>{page--;render();});
document.getElementById('next').addEventListener('click',()=>{page++;render();});
document.getElementById('refresh').addEventListener('click',e=>{
  const b=e.target;b.textContent='Yenilənir...';
  load().catch(er=>MH.toast(er.message,'err')).finally(()=>{b.textContent='Yenilə';});
});
document.getElementById('exp').addEventListener('click',()=>{
  const rows=[['Vaxt','Səviyyə','Mənbə','Mesaj','Detallar'],...view().map(r=>[r[0],L[r[1]],r[2],r[3],r[4]])];
  const csv='﻿'+rows.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\n');
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));a.download='maklerhub-loqlar.csv';a.click();
});
load().catch(er=>MH.toast(er.message,'err'));
setInterval(()=>{ if(!document.hidden && !tb.querySelector('tr.open')) load().catch(()=>{}); }, 60000);
