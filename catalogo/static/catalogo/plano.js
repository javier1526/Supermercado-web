"use strict";

const visor = document.getElementById("visor-plano");
const controles = document.getElementById("controles-plano");

if (visor && controles) {
  const imagen = visor.querySelector("img");
  const indicador = document.getElementById("escala-plano");
  const ampliar = document.getElementById("ampliar-plano");
  const reducir = document.getElementById("reducir-plano");
  const restablecer = document.getElementById("restablecer-plano");
  let escala = 100;

  function mostrar_escala(nueva_escala) {
    escala = Math.max(100, Math.min(300, nueva_escala));
    imagen.style.width = `${escala}%`;
    indicador.textContent = `${escala}%`;
    reducir.disabled = escala === 100;
    ampliar.disabled = escala === 300;
  }

  ampliar.addEventListener("click", () => mostrar_escala(escala + 50));
  reducir.addEventListener("click", () => mostrar_escala(escala - 50));
  restablecer.addEventListener("click", () => {
    mostrar_escala(100);
    visor.scrollTo(0, 0);
  });
  controles.hidden = false;
  mostrar_escala(100);
}
