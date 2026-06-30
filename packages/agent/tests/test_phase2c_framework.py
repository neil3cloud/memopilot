"""Test Phase 2c: Framework detection and tagging."""

import pytest
from agent.framework_detector import FrameworkDetector


class TestReactDetection:
    def test_react_component_pascalcase(self):
        """React components start with capital letter."""
        assert FrameworkDetector.detect_react_component("OrderForm", "")
        assert FrameworkDetector.detect_react_component("Button", "")
        assert not FrameworkDetector.detect_react_component("button", "")

    def test_react_hook_pattern(self):
        """React hooks start with 'use'."""
        assert FrameworkDetector.detect_react_hook("useState")
        assert FrameworkDetector.detect_react_hook("useEffect")
        assert FrameworkDetector.detect_react_hook("useContext")
        assert FrameworkDetector.detect_react_hook("useMyCustomHook")
        assert not FrameworkDetector.detect_react_hook("usecss")  # lowercase after 'use'
        assert not FrameworkDetector.detect_react_hook("state")


class TestAngularDetection:
    def test_angular_component(self):
        """Detect Angular @Component decorator."""
        source = """
@Component({
  selector: 'app-order',
  template: '<div></div>'
})
class OrderComponent { }
"""
        assert FrameworkDetector.detect_angular_component(source, "OrderComponent")

    def test_angular_service(self):
        """Detect Angular @Injectable decorator."""
        source = """
@Injectable({
  providedIn: 'root'
})
class OrderService { }
"""
        assert FrameworkDetector.detect_angular_service(source, "OrderService")

    def test_angular_module(self):
        """Detect Angular @NgModule decorator."""
        source = """
@NgModule({
  declarations: [OrderComponent],
  imports: [CommonModule]
})
class OrderModule { }
"""
        assert FrameworkDetector.detect_angular_module(source, "OrderModule")

    def test_angular_decorator_is_symbol_scoped(self):
        """Only the decorated class should be tagged, not every class in the file."""
        source = """
@Component({ selector: 'app-order', template: '<div></div>' })
class OrderComponent {}

class Helper {}
"""
        assert FrameworkDetector.detect_angular_component(source, "OrderComponent")
        assert not FrameworkDetector.detect_angular_component(source, "Helper")


class TestAPIDetection:
    def test_fetch_api(self):
        """Detect fetch() calls."""
        source = """
async function fetchOrders() {
    const response = await fetch('/api/orders');
    return response.json();
}
"""
        assert FrameworkDetector.detect_api_client(source)

    def test_axios_api(self):
        """Detect axios usage."""
        source = """
import axios from 'axios';

export const orderClient = axios.create({
    baseURL: '/api'
});
"""
        assert FrameworkDetector.detect_api_client(source)

    def test_httpclient(self):
        """Detect HttpClient (Angular)."""
        source = """
constructor(private http: HttpClient) { }

getOrder(id: number) {
    return this.http.get(`/api/orders/${id}`);
}
"""
        assert FrameworkDetector.detect_api_client(source)


class TestRouteDetection:
    def test_express_route_handler(self):
        """Detect Express.js route handlers."""
        source = """
app.get('/orders', (req, res) => {
    res.json([]);
});
"""
        assert FrameworkDetector.detect_route_handler(source, "handler")

    def test_decorator_route(self):
        """Detect decorator-style route handlers."""
        source = """
@get('/orders')
getOrders() {
    return [];
}
"""
        assert FrameworkDetector.detect_route_handler(source, "getOrders")


class TestGetTags:
    def test_react_component_tags(self):
        """Get tags for React component."""
        source = """
import React from 'react';

export function OrderForm() {
    return <form></form>;
}
"""
        tags = FrameworkDetector.get_tags("OrderForm", "function", source, "src/OrderForm.tsx")
        assert "react_component" in tags

    def test_react_hook_tags(self):
        """Get tags for React hook."""
        tags = FrameworkDetector.get_tags("useState", "function", "", "src/hooks.tsx")
        assert "react_hook" in tags

    def test_multiple_tags(self):
        """File can have multiple tags."""
        source = """
import axios from 'axios';

export function OrderForm() {
    return <form></form>;
}
"""
        tags = FrameworkDetector.get_tags("OrderForm", "function", source, "src/OrderForm.tsx")
        assert "react_component" in tags
        assert "api_client" in tags
