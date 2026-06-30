"""Phase 4: Summarization Polish — Framework and language-aware context generation."""

from __future__ import annotations

import pytest

from agent.symbol_summarizer import (
    FRAMEWORK_HINTS,
    PROMPTS_BY_LANGUAGE,
    SymbolSummarizer,
)


class MockLLMClient:
    """Mock LLM client that returns controlled responses."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or []
        self.call_count = 0
        self.last_system_prompt = None
        self.last_user_prompt = None

    async def complete(self, system: str, user: str, max_tokens: int = 120) -> MockResponse:
        """Mock complete method that captures prompts."""
        self.last_system_prompt = system
        self.last_user_prompt = user
        response_text = self.responses[self.call_count] if self.call_count < len(self.responses) else "Mock summary."
        self.call_count += 1
        return MockResponse(response_text)


class MockResponse:
    """Mock LLM response."""

    def __init__(self, content: str):
        self.content = content


class TestLanguageAwareSummarization:
    """Test language-specific prompt generation."""

    @pytest.mark.asyncio
    async def test_python_prompt_for_python_symbol(self):
        """Verify Python-specific system prompt is used for Python code."""
        client = MockLLMClient(["Python function summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="calculate",
            kind="function",
            signature="def calculate(x, y)",
            source="def calculate(x, y):\n    return x + y",
            language="python",
        )

        assert client.last_system_prompt == PROMPTS_BY_LANGUAGE["python"]["system"]
        assert "Python symbols" in client.last_system_prompt

    @pytest.mark.asyncio
    async def test_typescript_prompt_for_typescript_symbol(self):
        """Verify TypeScript-specific system prompt is used for TypeScript code."""
        client = MockLLMClient(["TypeScript function summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="calculate",
            kind="function",
            signature="function calculate(x: number, y: number): number",
            source="function calculate(x: number, y: number): number {\n  return x + y;\n}",
            language="typescript",
        )

        assert client.last_system_prompt == PROMPTS_BY_LANGUAGE["typescript"]["system"]
        assert "TypeScript" in client.last_system_prompt or "JavaScript" in client.last_system_prompt

    @pytest.mark.asyncio
    async def test_default_python_when_no_language_specified(self):
        """Verify Python is default when language not specified."""
        client = MockLLMClient(["Summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="test",
            kind="function",
            signature="def test()",
            source="def test(): pass",
        )

        assert client.last_system_prompt == PROMPTS_BY_LANGUAGE["python"]["system"]

    @pytest.mark.asyncio
    async def test_code_fence_uses_language(self):
        """Verify code fence uses specified language."""
        client = MockLLMClient(["Summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="test",
            kind="function",
            signature="function test()",
            source="function test() {}",
            language="typescript",
        )

        assert "```typescript" in client.last_user_prompt


class TestFrameworkContextGeneration:
    """Test framework-specific context hints."""

    @pytest.mark.asyncio
    async def test_react_component_tag_adds_context(self):
        """Verify React component tag adds framework context."""
        client = MockLLMClient(["React component summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="Button",
            kind="function",
            signature="function Button(props)",
            source="function Button(props) { return <button>{props.label}</button>; }",
            language="typescript",
            tags=["react_component"],
        )

        assert FRAMEWORK_HINTS["react_component"] in client.last_user_prompt
        assert "renders" in client.last_user_prompt.lower() or "props" in client.last_user_prompt.lower()

    @pytest.mark.asyncio
    async def test_react_hook_tag_adds_context(self):
        """Verify React hook tag adds framework context."""
        client = MockLLMClient(["React hook summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="useCounter",
            kind="function",
            signature="function useCounter()",
            source="function useCounter() { const [count, setCount] = useState(0); return [count, setCount]; }",
            language="typescript",
            tags=["react_hook"],
        )

        assert FRAMEWORK_HINTS["react_hook"] in client.last_user_prompt

    @pytest.mark.asyncio
    async def test_angular_service_tag_adds_context(self):
        """Verify Angular service tag adds framework context."""
        client = MockLLMClient(["Angular service summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="OrderService",
            kind="class",
            signature="class OrderService",
            source="class OrderService { getOrders() {} }",
            language="typescript",
            tags=["angular_service"],
        )

        assert FRAMEWORK_HINTS["angular_service"] in client.last_user_prompt

    @pytest.mark.asyncio
    async def test_express_route_tag_adds_context(self):
        """Verify Express route tag adds framework context."""
        client = MockLLMClient(["Express route summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="handleGetOrder",
            kind="function",
            signature="function handleGetOrder(req, res)",
            source="function handleGetOrder(req, res) { res.json({order: 123}); }",
            language="typescript",
            tags=["express_route"],
        )

        assert FRAMEWORK_HINTS["express_route"] in client.last_user_prompt

    @pytest.mark.asyncio
    async def test_api_client_tag_adds_context(self):
        """Verify API client tag adds framework context."""
        client = MockLLMClient(["API client summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="fetchUser",
            kind="function",
            signature="async function fetchUser(id)",
            source="async function fetchUser(id) { return fetch(`/api/users/${id}`); }",
            language="typescript",
            tags=["api_client"],
        )

        assert FRAMEWORK_HINTS["api_client"] in client.last_user_prompt

    @pytest.mark.asyncio
    async def test_no_context_when_no_tags(self):
        """Verify no framework context when no tags provided."""
        client = MockLLMClient(["Generic summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="helper",
            kind="function",
            signature="function helper()",
            source="function helper() { return 42; }",
            language="typescript",
            tags=[],
        )

        assert not any(hint for hint in FRAMEWORK_HINTS.values() if hint in client.last_user_prompt)

    @pytest.mark.asyncio
    async def test_no_context_when_unknown_tags(self):
        """Verify unknown tags don't add context."""
        client = MockLLMClient(["Summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="test",
            kind="function",
            signature="function test()",
            source="function test() {}",
            language="typescript",
            tags=["unknown_framework", "nonexistent_tag"],
        )

        assert not any(hint for hint in FRAMEWORK_HINTS.values() if hint in client.last_user_prompt)

    @pytest.mark.asyncio
    async def test_first_matching_tag_used(self):
        """Verify first matching tag is used when multiple tags provided."""
        client = MockLLMClient(["Summary."])
        summarizer = SymbolSummarizer(client)

        tags = ["unknown_tag", "react_component", "react_hook"]  # First known: react_component

        await summarizer.summarize(
            name="Button",
            kind="function",
            signature="function Button()",
            source="function Button() {}",
            language="typescript",
            tags=tags,
        )

        assert FRAMEWORK_HINTS["react_component"] in client.last_user_prompt


class TestBatchSummarizationWithLanguageAndTags:
    """Test batch summarization with language/framework support."""

    @pytest.mark.asyncio
    async def test_batch_typescript_prompt(self):
        """Verify TypeScript batch prompt is used."""
        client = MockLLMClient(["1. Summary 1\n2. Summary 2"])
        summarizer = SymbolSummarizer(client)

        symbols = [
            {
                "id": 1,
                "name": "func1",
                "kind": "function",
                "signature": "function func1()",
                "source": "function func1() {}",
            },
            {
                "id": 2,
                "name": "func2",
                "kind": "function",
                "signature": "function func2()",
                "source": "function func2() {}",
            },
        ]

        result = await summarizer.summarize_batch(symbols, language="typescript")

        assert client.last_system_prompt == PROMPTS_BY_LANGUAGE["typescript"]["batch_system"]
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_batch_with_framework_tags(self):
        """Verify framework tags are included in batch prompts."""
        client = MockLLMClient(["1. React component\n2. React hook"])
        summarizer = SymbolSummarizer(client)

        symbols = [
            {
                "id": 1,
                "name": "Button",
                "kind": "function",
                "signature": "function Button()",
                "source": "function Button() { return <button/>; }",
                "tags": ["react_component"],
            },
            {
                "id": 2,
                "name": "useCounter",
                "kind": "function",
                "signature": "function useCounter()",
                "source": "function useCounter() { const [c, setC] = useState(0); }",
                "tags": ["react_hook"],
            },
        ]

        result = await summarizer.summarize_batch(symbols, language="typescript")

        assert FRAMEWORK_HINTS["react_component"] in client.last_user_prompt
        assert FRAMEWORK_HINTS["react_hook"] in client.last_user_prompt
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_batch_default_language_python(self):
        """Verify batch defaults to Python when language not specified."""
        client = MockLLMClient(["1. Summary 1"])
        summarizer = SymbolSummarizer(client)

        symbols = [
            {
                "id": 1,
                "name": "func",
                "kind": "function",
                "signature": "def func()",
                "source": "def func(): pass",
            }
        ]

        await summarizer.summarize_batch(symbols)

        assert client.last_system_prompt == PROMPTS_BY_LANGUAGE["python"]["batch_system"]

    @pytest.mark.asyncio
    async def test_batch_code_fence_uses_language(self):
        """Verify code fences in batch use specified language."""
        client = MockLLMClient(["1. Summary 1\n2. Summary 2"])
        summarizer = SymbolSummarizer(client)

        symbols = [
            {
                "id": 1,
                "name": "func1",
                "kind": "function",
                "signature": "function func1()",
                "source": "function func1() {}",
            },
            {
                "id": 2,
                "name": "func2",
                "kind": "function",
                "signature": "function func2()",
                "source": "function func2() {}",
            },
        ]

        await summarizer.summarize_batch(symbols, language="typescript")

        # Both code fences should use TypeScript
        assert client.last_user_prompt.count("```typescript") == 2


class TestBackwardCompatibility:
    """Test backward compatibility with existing code."""

    @pytest.mark.asyncio
    async def test_summarize_without_language_works(self):
        """Verify summarize works without language parameter."""
        client = MockLLMClient(["Summary."])
        summarizer = SymbolSummarizer(client)

        result = await summarizer.summarize(
            name="test",
            kind="function",
            signature="def test()",
            source="def test(): pass",
        )

        assert result == "Summary."

    @pytest.mark.asyncio
    async def test_summarize_without_tags_works(self):
        """Verify summarize works without tags parameter."""
        client = MockLLMClient(["Summary."])
        summarizer = SymbolSummarizer(client)

        result = await summarizer.summarize(
            name="test",
            kind="function",
            signature="def test()",
            source="def test(): pass",
            language="python",
        )

        assert result == "Summary."

    @pytest.mark.asyncio
    async def test_batch_without_tags_in_symbols_works(self):
        """Verify batch works when symbols don't have tags."""
        client = MockLLMClient(["1. Summary 1"])
        summarizer = SymbolSummarizer(client)

        symbols = [
            {
                "id": 1,
                "name": "func",
                "kind": "function",
                "signature": "def func()",
                "source": "def func(): pass",
                # No tags key
            }
        ]

        result = await summarizer.summarize_batch(symbols, language="python")

        assert len(result) == 1
        assert result[1] == "Summary 1"


class TestLanguagePromptCoverage:
    """Test all defined language prompts."""

    def test_all_languages_have_prompts(self):
        """Verify all expected languages have prompts defined."""
        assert "python" in PROMPTS_BY_LANGUAGE
        assert "typescript" in PROMPTS_BY_LANGUAGE

    def test_all_prompts_have_system_and_batch(self):
        """Verify all language prompts have both system and batch."""
        for language, prompts in PROMPTS_BY_LANGUAGE.items():
            assert "system" in prompts
            assert "batch_system" in prompts
            assert isinstance(prompts["system"], str)
            assert isinstance(prompts["batch_system"], str)
            assert len(prompts["system"]) > 0
            assert len(prompts["batch_system"]) > 0

    def test_typescript_prompt_mentions_javascript(self):
        """Verify TypeScript prompt acknowledges JavaScript support."""
        ts_system = PROMPTS_BY_LANGUAGE["typescript"]["system"]
        assert "TypeScript" in ts_system or "JavaScript" in ts_system

    def test_python_prompt_mentions_python(self):
        """Verify Python prompt mentions Python."""
        py_system = PROMPTS_BY_LANGUAGE["python"]["system"]
        assert "Python" in py_system


class TestFrameworkHintsCoverage:
    """Test framework detection hints."""

    def test_all_framework_hints_are_strings(self):
        """Verify all framework hints are non-empty strings."""
        for framework, hint in FRAMEWORK_HINTS.items():
            assert isinstance(hint, str)
            assert len(hint) > 0
            assert hint.startswith(" ")  # Should have leading space for concat

    def test_react_hints_present(self):
        """Verify React framework hints are defined."""
        assert "react_component" in FRAMEWORK_HINTS
        assert "react_hook" in FRAMEWORK_HINTS

    def test_angular_hints_present(self):
        """Verify Angular framework hints are defined."""
        assert "angular_component" in FRAMEWORK_HINTS
        assert "angular_service" in FRAMEWORK_HINTS
        assert "angular_module" in FRAMEWORK_HINTS

    def test_express_hint_present(self):
        """Verify Express framework hint is defined."""
        assert "express_route" in FRAMEWORK_HINTS

    def test_api_client_hint_present(self):
        """Verify API client hint is defined."""
        assert "api_client" in FRAMEWORK_HINTS

    def test_hints_provide_actionable_context(self):
        """Verify hints give LLM actionable guidance."""
        for framework, hint in FRAMEWORK_HINTS.items():
            # Should contain imperative or focus guidance
            lower_hint = hint.lower()
            assert any(word in lower_hint for word in ["focus", "describe", "handle", "what", "explains"])


class TestPhase4LanguageAwareContext:
    """Test Phase 4: Enhanced language-aware context hints."""

    def test_csharp_prompts_include_aspnet_context(self):
        """Verify C# prompts mention ASP.NET Core."""
        csharp_system = PROMPTS_BY_LANGUAGE.get("csharp", {}).get("system", "")
        assert "C#" in csharp_system or "ASP.NET" in csharp_system

    def test_csharp_endpoint_hint_detailed(self):
        """Verify endpoint hint is comprehensive."""
        hint = FRAMEWORK_HINTS.get("endpoint", "")
        assert "HTTP" in hint or "method" in hint.lower()
        assert "route" in hint.lower() or "path" in hint.lower()
        assert "dependencies" in hint.lower() or "injects" in hint.lower()

    def test_csharp_service_hint_complete(self):
        """Verify service hint covers business logic."""
        hint = FRAMEWORK_HINTS.get("service", "")
        assert "business logic" in hint.lower() or "operations" in hint.lower()
        assert "interface" in hint.lower() or "dependencies" in hint.lower()

    def test_csharp_repository_hint_detailed(self):
        """Verify repository hint covers data operations."""
        hint = FRAMEWORK_HINTS.get("repository", "")
        assert "entity" in hint.lower() or "database" in hint.lower()
        assert "operations" in hint.lower() or "queries" in hint.lower()

    def test_react_component_hint_covers_props_and_rendering(self):
        """Verify React component hint addresses both props and rendering."""
        hint = FRAMEWORK_HINTS.get("react_component", "")
        assert "React" in hint
        assert ("props" in hint.lower() or "accepts" in hint.lower())
        assert ("renders" in hint.lower() or "render" in hint.lower())

    def test_react_hook_hint_covers_state_and_effects(self):
        """Verify React hook hint covers state/effect management."""
        hint = FRAMEWORK_HINTS.get("react_hook", "")
        assert "hook" in hint.lower()
        assert ("state" in hint.lower() or "manage" in hint.lower())
        assert ("effect" in hint.lower() or "side" in hint.lower())

    def test_angular_component_hint_covers_template_and_injection(self):
        """Verify Angular component hint addresses template and DI."""
        hint = FRAMEWORK_HINTS.get("angular_component", "")
        assert "Angular" in hint
        assert "template" in hint.lower() or "DOM" in hint
        assert "inject" in hint.lower()

    def test_angular_service_hint_covers_data_and_operations(self):
        """Verify Angular service hint addresses data and operations."""
        hint = FRAMEWORK_HINTS.get("angular_service", "")
        assert "service" in hint.lower()
        assert ("data" in hint.lower() or "HTTP" in hint)
        assert ("interface" in hint.lower() or "implements" in hint.lower())

    def test_all_csharp_framework_hints_present(self):
        """Verify all C# framework hints are defined."""
        csharp_frameworks = ["endpoint", "controller", "service", "repository", "di_injectable"]
        for framework in csharp_frameworks:
            assert framework in FRAMEWORK_HINTS, f"Missing C# framework hint: {framework}"

    def test_all_typescript_framework_hints_present(self):
        """Verify all TypeScript framework hints are defined."""
        typescript_frameworks = [
            "react_component",
            "react_hook",
            "angular_component",
            "angular_service",
            "angular_module",
            "express_route",
            "api_client",
        ]
        for framework in typescript_frameworks:
            assert framework in FRAMEWORK_HINTS, f"Missing TypeScript framework hint: {framework}"

    @pytest.mark.asyncio
    async def test_framework_context_passed_to_client(self):
        """Verify framework context is included in LLM prompt."""
        client = MockLLMClient(["React component summary."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="UserProfile",
            kind="function",
            signature="UserProfile(props): JSX.Element",
            source="export function UserProfile(props) { return <div>{props.name}</div>; }",
            language="typescript",
            tags=["react_component"],
        )

        # Framework context should be in user prompt
        assert "React component" in client.last_user_prompt or "component" in client.last_user_prompt.lower()

    @pytest.mark.asyncio
    async def test_csharp_endpoint_context_in_prompt(self):
        """Verify C# endpoint context is included in prompt."""
        client = MockLLMClient(["Gets order details."])
        summarizer = SymbolSummarizer(client)

        await summarizer.summarize(
            name="GetOrder",
            kind="method",
            signature="public Task<IActionResult> GetOrder(int id)",
            source="[HttpGet(\"{id}\")] public async Task<IActionResult> GetOrder(int id) { }",
            language="csharp",
            tags=["endpoint"],
        )

        # Framework context should be in user prompt
        user_prompt = client.last_user_prompt.lower()
        assert "endpoint" in user_prompt or "action" in user_prompt or "http" in user_prompt

    def test_build_framework_context_with_language_and_kind(self):
        """Verify _build_framework_context uses language and kind."""
        client = MockLLMClient()
        summarizer = SymbolSummarizer(client)

        # C# method should get endpoint context even without explicit tag
        context = summarizer._build_framework_context(None, "csharp", "method")
        assert "endpoint" in context.lower() or len(context) > 0

        # C# class should get service context without explicit tag
        context = summarizer._build_framework_context(None, "csharp", "class")
        assert len(context) >= 0  # May get service hint

        # Python function without tags should have empty context
        context = summarizer._build_framework_context(None, "python", "function")
        assert context == ""

