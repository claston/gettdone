(function () {
  const form = document.getElementById("contact-form");
  const feedback = document.getElementById("contact-feedback");
  if (!form || !feedback) {
    return;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    const name = String(document.getElementById("name").value || "").trim();
    const email = String(document.getElementById("email").value || "").trim();
    const subject = String(document.getElementById("subject").value || "").trim();
    const message = String(document.getElementById("message").value || "").trim();
    const attachmentInput = document.getElementById("attachment");
    const attachmentName = attachmentInput && attachmentInput.files && attachmentInput.files[0]
      ? String(attachmentInput.files[0].name || "").trim()
      : "";

    const mailSubject = encodeURIComponent("Suporte OFX Simples | " + (subject || "Contato"));
    const mailBody = encodeURIComponent(
      "Nome: " + name + "\n" +
      "Email: " + email + "\n" +
      "Assunto: " + subject + "\n\n" +
      (attachmentName ? "Arquivo informado: " + attachmentName + "\n\n" : "") +
      message
    );

    feedback.textContent = "Abrindo seu cliente de e-mail para enviar a mensagem...";
    window.location.href = "mailto:suporte@ofxsimples.com?subject=" + mailSubject + "&body=" + mailBody;
  });
})();
