(function () {
  const statusMsg = document.getElementById("status-msg");

  function setStatus(message, kind) {
    if (!statusMsg) {
      return;
    }
    statusMsg.textContent = message || "";
    statusMsg.className = "status";
    if (kind) {
      statusMsg.classList.add(kind);
    }
  }

  function getSafeNextPath(params) {
    const raw = String(params.get("next") || "").trim();
    if (!raw.startsWith("/")) {
      return "/client-area.html";
    }
    return raw;
  }

  const params = new URLSearchParams(window.location.search);
  const userToken = String(params.get("user_token") || "").trim();
  const error = String(params.get("error") || "").trim();
  const nextPath = getSafeNextPath(params);

  if (userToken) {
    localStorage.setItem("gettdone_user_token", userToken);
    setStatus("Login com Google concluido. Redirecionando...", "success");
    window.setTimeout(() => {
      window.location.href = nextPath;
    }, 120);
    return;
  }

  if (error) {
    setStatus("Nao foi possivel concluir o login com Google. Tente novamente.", "error");
  } else {
    setStatus("Resposta de autenticacao invalida.", "error");
  }

  window.setTimeout(() => {
    window.location.href = `./login.html?next=${encodeURIComponent(nextPath)}`;
  }, 1200);
})();
