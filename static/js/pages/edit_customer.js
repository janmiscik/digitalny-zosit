/* =====================================
   VYHĽADANIE PODĽA IČO (auto-doplnenie)
   ===================================== */

async function lookupCustomerIco(prefix) {

    const icoInput = document.getElementById(prefix + "-customer-ico");
    const statusEl = document.getElementById(prefix + "-customer-ico-status");

    const ico = icoInput.value.trim();

    if (!ico) {

        statusEl.textContent = "Najprv zadaj IČO.";
        statusEl.style.color = "var(--danger, #c0392b)";
        return;

    }

    statusEl.textContent = "Hľadám...";
    statusEl.style.color = "var(--text-muted)";

    try {

        const response = await fetch(`/customers/lookup-ico/${encodeURIComponent(ico)}`);

        if (!response.ok) {

            statusEl.textContent = "Nenašlo sa - vyplň údaje ručne.";
            statusEl.style.color = "var(--danger, #c0392b)";
            return;

        }

        const data = await response.json();

        const nameField = document.getElementById(prefix + "-customer-name");
        const addressField = document.getElementById(prefix + "-customer-address");
        const cityField = document.getElementById(prefix + "-customer-city");
        const zipField = document.getElementById(prefix + "-customer-zip_code");
        const dicField = document.getElementById(prefix + "-customer-dic");
        const icDphField = document.getElementById(prefix + "-customer-ic_dph");

        if (nameField && data.name) nameField.value = data.name;
        if (addressField && data.address) addressField.value = data.address;
        if (cityField && data.city) cityField.value = data.city;
        if (zipField && data.zip_code) zipField.value = data.zip_code;
        if (dicField && data.dic) dicField.value = data.dic;
        if (icDphField) icDphField.value = data.ic_dph || "";

        statusEl.textContent = "Nájdené a doplnené - skontroluj údaje.";
        statusEl.style.color = "var(--success, #2e7d32)";

    } catch (error) {

        statusEl.textContent = "Vyhľadávanie zlyhalo - vyplň údaje ručne.";
        statusEl.style.color = "var(--danger, #c0392b)";

    }

}


DZ.register({
    "lookup-customer-ico": (element) => lookupCustomerIco(element.dataset.arg)
});
