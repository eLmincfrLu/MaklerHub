const front=window.MH_IMG.front, left=window.MH_IMG.left, right=window.MH_IMG.right;
[front,left,right].forEach(u=>{const i=new Image();i.src=u;});

const heroEl=document.querySelector('.hero');
const spotlight=document.getElementById('spotlight');
heroEl.addEventListener('mousemove',(e)=>{
  const r=heroEl.getBoundingClientRect();
  spotlight.style.left=(e.clientX-r.left)+'px'; spotlight.style.top=(e.clientY-r.top)+'px';
  spotlight.style.opacity='1';
});
heroEl.addEventListener('mouseleave',()=>{spotlight.style.opacity='0';});

function countUp(el,target,suffix,duration){
  const t0=performance.now();
  function step(now){
    const p=Math.min((now-t0)/duration,1); const e=1-Math.pow(1-p,3);
    el.textContent=Math.round(e*target)+suffix;
    if(p<1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
document.querySelectorAll('.stat-chip .num').forEach((el,i)=>{
  const target=parseInt(el.dataset.target,10), suffix=el.dataset.suffix||'';
  setTimeout(()=>countUp(el,target,suffix,1100), 1300+i*160);
});
requestAnimationFrame(()=>heroEl.classList.add('intro'));

const bgA=document.getElementById('bgA'), bgB=document.getElementById('bgB');
let topLayer='A', currentUrl=front, locked=false;
function showImage(url){
  if(url===currentUrl) return;
  const top = topLayer==='A'?bgA:bgB, bottom = topLayer==='A'?bgB:bgA;
  bottom.style.backgroundImage=`url('${url}')`;
  requestAnimationFrame(()=>{ bottom.style.opacity=1; top.style.opacity=0; });
  topLayer = topLayer==='A'?'B':'A'; currentUrl=url;
}
const loginBtn=document.getElementById('loginBtn'), registerBtn=document.getElementById('registerBtn');
loginBtn.addEventListener('mouseenter',()=>{ if(!locked) showImage(left); });
registerBtn.addEventListener('mouseenter',()=>{ if(!locked) showImage(right); });
document.querySelector('.hero').addEventListener('mouseleave',()=>{ if(!locked) showImage(front); });

// Düyməyə klik: ekran qaralır və müvafiq səhifəyə keçid olunur
const fadeEl=document.getElementById('fade');
function go(url){
  if(locked) return; locked=true;
  fadeEl.style.transition='opacity .7s ease'; fadeEl.style.opacity=1;
  setTimeout(()=>{ location.href=url; },650);
}
loginBtn.addEventListener('click',()=>go(window.MH_HOME||window.MH_LOGIN));
registerBtn.addEventListener('click',()=>go(registerBtn.dataset.logout||window.MH_REGISTER));

// Brauzerin "geri" düyməsi ilə qayıdanda ekranı bərpa et
addEventListener('pageshow',e=>{
  if(e.persisted){ fadeEl.style.transition=''; fadeEl.style.opacity=0; locked=false; showImage(front); }
});
