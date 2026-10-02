document.querySelectorAll('.eye').forEach(function(icon){
  icon.addEventListener('click', function(){
    var inp = document.getElementById(icon.dataset.target);
    inp.type = inp.type === 'password' ? 'text' : 'password';
    icon.style.opacity = inp.type === 'text' ? '1' : '.55';
  });
});

var roleLabels = document.querySelectorAll('.role');
var licenseField = document.getElementById('licenseField');
function syncRole(){
  roleLabels.forEach(function(l){
    var inp = l.querySelector('input');
    l.classList.toggle('active', inp.checked);
    if (inp.checked && licenseField) licenseField.style.display = inp.value === 'makler' ? 'block' : 'none';
  });
}
roleLabels.forEach(function(label){
  label.querySelector('input').addEventListener('change', syncRole);
});
if (roleLabels.length) syncRole();
