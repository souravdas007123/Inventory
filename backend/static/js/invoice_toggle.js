document.addEventListener('DOMContentLoaded', function() {
    // Select the dropdown and the Order field
    const modeSelect = document.querySelector('#id_invoice_mode');
    const orderFieldRow = document.querySelector('.field-order'); // Django admin places the field inside this class

    function toggleFields() {
        if (modeSelect.value === 'offline') {
            // If Offline, hide the Order field
            if (orderFieldRow) {
                orderFieldRow.style.display = 'none';
            }
            // If an order was already selected, clear it
            const orderDropdown = document.querySelector('#id_order');
            if (orderDropdown) {
                orderDropdown.value = '';
            }
        } else {
            // If Online, show the Order field
            if (orderFieldRow) {
                orderFieldRow.style.display = ''; // Restore default display
            }
        }
    }

    // Check on page load (useful when editing)
    if (modeSelect) {
        toggleFields();
        
        // Check whenever the dropdown changes
        modeSelect.addEventListener('change', toggleFields);
    }
});