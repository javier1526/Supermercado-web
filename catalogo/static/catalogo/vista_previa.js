const archivo = document.querySelector("#id_fotografia_comercial");
const vistaPrevia = document.querySelector("#vista-previa");
const imagen = vistaPrevia.querySelector("img");
const nombreFotografia = vistaPrevia.querySelector("#nombre-fotografia");
let urlImagen;

archivo.addEventListener("change", () => {
  if (urlImagen) URL.revokeObjectURL(urlImagen);
  vistaPrevia.hidden = true;
  imagen.removeAttribute("src");
  const fotografia = archivo.files[0];
  if (fotografia) {
    nombreFotografia.textContent = fotografia.name;
    urlImagen = URL.createObjectURL(fotografia);
    imagen.src = urlImagen;
  }
});

imagen.addEventListener("load", () => {
  vistaPrevia.hidden = false;
});
