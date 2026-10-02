function countUp(el,target,duration){
  const t0=performance.now();
  function step(now){
    const p=Math.min((now-t0)/duration,1); const e=1-Math.pow(1-p,3);
    el.textContent=Math.round(e*target);
    if(p<1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
document.querySelectorAll('.stat .n').forEach((el,i)=>{
  setTimeout(()=>countUp(el,parseInt(el.dataset.target,10),1000), 500+i*120);
});
document.querySelectorAll('.match .ring').forEach((ring,i)=>{
  const final=parseFloat(ring.dataset.final);
  const pctEl=ring.closest('.match').querySelector('.pct');
  const target=parseInt(pctEl.dataset.target,10);
  setTimeout(()=>{
    ring.style.transition='stroke-dashoffset 1s cubic-bezier(.2,.7,.2,1)';
    ring.style.strokeDashoffset=final;
    const t0=performance.now();
    function step(now){
      const p=Math.min((now-t0)/1000,1); const e=1-Math.pow(1-p,3);
      pctEl.textContent=Math.round(e*target)+'%';
      if(p<1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }, 700+i*130);
});

// Kartı klikləyəndə elanın detalları açılır
document.querySelectorAll('.card[data-match]').forEach(card=>{
  card.style.cursor='pointer';
  card.addEventListener('click',()=>MH.showMatch(card.dataset.match));
});
