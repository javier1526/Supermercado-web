const campoFotografia = document.getElementById("id_fotografia_referencial");
const vistaPrevia = document.getElementById("vista-previa-ubicacion");
let direccionFotografia;

campoFotografia.addEventListener("change", () => {
  if (direccionFotografia) URL.revokeObjectURL(direccionFotografia);
  const archivo = campoFotografia.files[0];
  vistaPrevia.hidden = true;
  if (archivo && ["image/jpeg", "image/png"].includes(archivo.type)) {
    direccionFotografia = URL.createObjectURL(archivo);
    const imagen = vistaPrevia.querySelector("img");
    imagen.onload = () => { vistaPrevia.hidden = false; };
    imagen.onerror = () => { vistaPrevia.hidden = true; };
    imagen.src = direccionFotografia;
  }
});
