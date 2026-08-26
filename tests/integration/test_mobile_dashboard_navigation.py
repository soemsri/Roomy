def test_dashboard_has_mobile_navigation_controls(client):
    response = client.get(
        "/admin/dashboard#leaseSection",
        cookies={"admin_session": "test_session_token"},
    )

    assert response.status_code == 200
    assert 'id="mobileMenuToggle"' in response.text
    assert 'aria-controls="adminSidebar"' in response.text
    assert 'id="adminSidebar"' in response.text
    assert 'id="sidebarOverlay"' in response.text
    assert "function toggleMobileSidebar()" in response.text
    assert 'href="#leaseSection"' in response.text
