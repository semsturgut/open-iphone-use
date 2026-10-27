document.querySelectorAll('.copy-command').forEach((button) => {
  let reset;
  button.addEventListener('click', async () => {
    const command = button.parentElement.querySelector('code').textContent;
    let copied = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(command);
        copied = true;
      }
    } catch (_) { /* Try the fallback below. */ }
    if (!copied) {
      const field = document.createElement('textarea');
      field.value = command;
      field.setAttribute('readonly', '');
      field.style.position = 'fixed';
      field.style.left = '-9999px';
      document.body.appendChild(field);
      field.select();
      try { copied = document.execCommand('copy'); } catch (_) { /* Show failure. */ }
      field.remove();
      button.focus();
    }
    button.textContent = copied ? 'Copied' : 'Copy failed';
    button.setAttribute('aria-label', copied ? 'Copied to clipboard' : 'Copy failed; select the command');
    clearTimeout(reset);
    reset = setTimeout(() => {
      button.textContent = 'Copy';
      button.setAttribute('aria-label', `Copy ${button.closest('.command').querySelector('h3').textContent.toLowerCase()} command`);
    }, 2000);
  });
});
