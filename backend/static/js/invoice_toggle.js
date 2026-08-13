document.addEventListener('DOMContentLoaded', function() {
    // Dropdown aur Order field ko select karein
    const modeSelect = document.querySelector('#id_invoice_mode');
    const orderFieldRow = document.querySelector('.field-order'); // Django admin is class ke andar field rakhta hai

    function toggleFields() {
        if (modeSelect.value === 'offline') {
            // Agar Offline hai toh Order wale dabbe ko hide kar dein
            if (orderFieldRow) {
                orderFieldRow.style.display = 'none';
            }
            // Agar pehle se koi order select tha, toh use clear kar dein
            const orderDropdown = document.querySelector('#id_order');
            if (orderDropdown) {
                orderDropdown.value = '';
            }
        } else {
            // Agar Online hai toh Order wale dabbe ko show karein
            if (orderFieldRow) {
                orderFieldRow.style.display = ''; // Default display wapas le aayein
            }
        }
    }

    // Page load hone par check karein (Edit karte time kaam aayega)
    if (modeSelect) {
        toggleFields();
        
        // Jab bhi dropdown change ho, tab check karein
        modeSelect.addEventListener('change', toggleFields);
    }
});