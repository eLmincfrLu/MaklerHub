setTimeout(()=>{
  document.querySelectorAll('.bar').forEach((b,i)=>{
    setTimeout(()=>{ b.style.height=b.dataset.h+'%'; }, i*45);
  });
}, 350);

setTimeout(()=>{
  document.querySelectorAll('.donut .seg').forEach(seg=>{
    const final=parseFloat(seg.dataset.final);
    const rot=seg.dataset.rot;
    if(rot) seg.style.transformOrigin='85px 85px', seg.style.transform=`rotate(${rot}deg)`;
    seg.style.transition='stroke-dashoffset 1.1s cubic-bezier(.2,.7,.2,1)';
    seg.style.strokeDashoffset=final;
  });
}, 500);

setTimeout(()=>{
  document.querySelectorAll('.rfill').forEach((f,i)=>{
    setTimeout(()=>{ f.style.width=f.dataset.w+'%'; }, i*110);
  });
}, 750);

document.getElementById('rangeSel').addEventListener('change',e=>{
  location.href=location.pathname+'?range='+e.target.value;
});
