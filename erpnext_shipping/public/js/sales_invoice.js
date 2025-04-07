frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        let shipment_con = $('.document-link-badge[data-doctype="Shipment"]') // find the shipment connection
        
        // if connection exists
        if (shipment_con && frm.doc.shipment) {
            let ships = frm.doc.shipment.split(", ") // separate shipments string and store as an array
            let ship_con_badge = $('.document-link-badge[data-doctype="Shipment"] .count') // find the count badge
            let ship_con_link = $('.document-link-badge[data-doctype="Shipment"] .badge-link') // find the shipment link

            ship_con_badge.html(ships.length) // display the count in the badge
            ship_con_badge.removeClass('hidden') // show the badge
            ship_con_link.off('click') // remove original click events for shipment link
            ship_con_link.click(function(event) {
                event.stopPropagation() // override all click events
                event.preventDefault()
                frappe.router.set_route('List', 'Shipment', { name: ['in',[ships]] })
            })
        }
    }
});