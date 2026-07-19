let currentStep = 1;
let selectedPlan = 'standard';

function goStep(n) {
  if (n > currentStep) {
    const err = validate(currentStep);
    if (err) { showErr(currentStep, err); return; }
    hideErr(currentStep);
  }

  const outgoing = document.getElementById(`step${currentStep}`);
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const activate = () => {
    document.getElementById(`step${currentStep}`).classList.remove('active');
    const prevInd = document.getElementById(`step-ind-${currentStep}`);
    prevInd.classList.remove('active');
    if (n > currentStep) {
      prevInd.classList.add('done');
      prevInd.querySelector('.step-num').textContent = '✓';
      const line = document.getElementById(`sl${currentStep}`);
      if (line) line.classList.add('done');
    }
    currentStep = n;
    const next = document.getElementById(`step${n}`);
    next.classList.add('active');
    next.style.animation = 'none';
    void next.offsetWidth;
    next.style.animation = '';
    const ind = document.getElementById(`step-ind-${n}`);
    ind.classList.add('active');
    ind.classList.remove('done');
    ind.querySelector('.step-num').textContent = n;
  };

  if (reduce || !outgoing) { activate(); return; }
  outgoing.style.animation = 'step-exit .28s ease both';
  setTimeout(activate, 260);
}

function validate(step) {
  if (step === 1) {
    if (!document.getElementById('org-name').value.trim())
      return 'Organisation name is required.';
    if (!document.getElementById('username').value.trim())
      return 'Username is required.';
    const pw = document.getElementById('password').value;
    if (!pw)
      return 'Password is required.';
    if (pw.length < 8)
      return 'Password must be at least 8 characters.';
    if (pw !== document.getElementById('confirm-password').value)
      return 'Passwords do not match.';
    const chatId = document.getElementById('chat-id').value.trim();
    if (!chatId)
      return 'Telegram Channel ID is required for file storage.';
    if (!/^-?\d+$/.test(chatId))
      return 'Telegram Channel ID must be a numeric ID (e.g. -1001234567890).';
  }
  if (step === 2) {
    if (!document.getElementById('contact-name').value.trim())
      return 'Contact name is required.';
    const email = document.getElementById('contact-email').value.trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
      return 'A valid work email is required.';
  }
  return null;
}

function showErr(step, msg) {
  const el = document.getElementById(`err${step}`);
  el.textContent = msg;
  el.classList.remove('show', 'shake');
  void el.offsetWidth;
  el.classList.add('show', 'shake');
}
function hideErr(step) {
  const el = document.getElementById(`err${step}`);
  if (el) { el.textContent=''; el.classList.remove('show'); }
}

function selectPlan(plan) {
  selectedPlan = plan;
  ['starter','standard','enterprise'].forEach(p => {
    document.getElementById(`plan-${p}`).classList.toggle('selected', p === plan);
  });
}

async function submitForm() {
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  const confirm = document.getElementById('confirm-password').value;

  if (!username) {
    showErr(1, 'Username is required.');
    return;
  }
  if (!password) {
    showErr(1, 'Password is required.');
    return;
  }
  if (password !== confirm) {
    showErr(1, 'Passwords do not match.');
    return;
  }

  const btn = document.getElementById('submit-btn');
  btn.disabled = true;
  btn.textContent = 'Submitting…';
  hideErr(3);

  const payload = {
    org_name:      document.getElementById('org-name').value.trim(),
    username:      username,
    password:      password,
    contact_name:  document.getElementById('contact-name').value.trim(),
    contact_email: document.getElementById('contact-email').value.trim(),
    contact_phone: document.getElementById('contact-phone').value.trim(),
    plan:          selectedPlan,
    chat_id:       document.getElementById('chat-id').value.trim(),
    channel_name:  document.getElementById('channel-name').value.trim(),
    message: [
      document.getElementById('org-message').value.trim(),
      document.getElementById('extra-note').value.trim(),
      document.getElementById('org-industry').value ? `Industry: ${document.getElementById('org-industry').value}` : '',
      document.getElementById('org-size').value ? `Size: ${document.getElementById('org-size').value}` : '',
      document.getElementById('contact-title').value.trim() ? `Title: ${document.getElementById('contact-title').value.trim()}` : '',
    ].filter(Boolean).join(' | '),
  };

  try {
    const r = await fetch('/api/org/register', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await r.json();
    if (!r.ok) {
      showErr(3, data.error || 'Submission failed. Please try again.');
      btn.disabled = false; btn.textContent = 'Submit Request ✓';
      return;
    }
    document.getElementById('steps-bar').style.display = 'none';
    ['step1','step2','step3'].forEach(id => document.getElementById(id).style.display='none');
    document.getElementById('success-ref').textContent = payload.org_name;
    const notice = document.getElementById('success-email-notice');
    if (notice && payload.contact_email) notice.textContent = `We'll keep ${payload.contact_email} on file as the primary contact.`;
    document.getElementById('success').classList.add('show');
    celebrate();
    setTimeout(() => { window.location.href = '/?registered=1'; }, 5000);
  } catch {
    showErr(3, 'Network error. Please check your connection and try again.');
    btn.disabled = false; btn.textContent = 'Submit Request ✓';
  }
}

// celebratory burst of particles on successful submit
function celebrate() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colors = ['#0ea5b7', '#7c3aed', '#2563eb', '#059669', '#d97706'];
  const burst = document.createElement('div');
  burst.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:999';
  document.body.appendChild(burst);
  const cx = window.innerWidth / 2, cy = window.innerHeight * 0.38;
  for (let i = 0; i < 70; i++) {
    const p = document.createElement('span');
    const angle = (Math.PI * 2 * i) / 70 + Math.random();
    const dist = 120 + Math.random() * 260;
    const dx = Math.cos(angle) * dist, dy = Math.sin(angle) * dist;
    const size = 5 + Math.random() * 7;
    p.style.cssText = `position:absolute;left:${cx}px;top:${cy}px;width:${size}px;height:${size}px;border-radius:${Math.random()>.5?'50%':'2px'};background:${colors[i%colors.length]};opacity:1;transform:translate(0,0) scale(1);transition:transform .9s cubic-bezier(.16,1,.3,1),opacity .9s ease`;
    burst.appendChild(p);
    requestAnimationFrame(() => {
      p.style.transform = `translate(${dx}px,${dy}px) scale(0)`;
      p.style.opacity = '0';
    });
  }
  setTimeout(() => burst.remove(), 1000);
}

// Password eye toggle
document.querySelectorAll('.password-toggle').forEach(toggle => {
  toggle.addEventListener('click', () => {
    const input = toggle.parentElement.querySelector('input');
    if (input.type === 'password') {
      input.type = 'text';
      toggle.textContent = '🙈';
    } else {
      input.type = 'password';
      toggle.textContent = '👁️';
    }
  });
});
