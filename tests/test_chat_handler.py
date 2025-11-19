"""
Unit tests for RAG Chat Application Lambda handler and core functions.
"""
import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.rag_lambda.chat_app_lambda import lambda_handler, main, build_chain, handle_turn


@pytest.fixture
def mock_lambda_event():
    """Mock Lambda event payload."""
    return {
        "conversation_id": "test-conv-123",
        "input_prompt": "What does version 2023.4 say about password rotation?",
        "retrieval_filters": {"document_type": ["Policy"], "version": ["2023.4"]},
        "user_id": "test-user@example.com",
        "chat_history_hint": ""
    }


@pytest.fixture
def mock_config():
    """Mock configuration values."""
    return {
        "AWS_REGION": "us-east-1",
        "KB_ID": "test-kb-id",
        "MODEL_ID": "anthropic.claude-3-5-sonnet-20240620-v1:0",
        "PG_DSN": "postgresql://user:pass@localhost:5432/testdb",
        "DEFAULT_TOP_K": 6
    }


class TestLambdaHandler:
    """Test Lambda handler function."""
    
    @patch('src.rag_lambda.chat_app_lambda.main')
    def test_lambda_handler_success(self, mock_main, mock_lambda_event):
        """Test successful Lambda handler invocation."""
        mock_main.return_value = {
            "answer": "Test answer",
            "citations": [{"title": "Test Doc", "type": "Policy"}]
        }
        
        result = lambda_handler(mock_lambda_event, None)
        
        assert result["statusCode"] == 200
        assert "answer" in json.loads(result["body"])
        mock_main.assert_called_once()
    
    @patch('src.rag_lambda.chat_app_lambda.main')
    def test_lambda_handler_with_api_gateway_body(self, mock_main, mock_lambda_event):
        """Test Lambda handler with API Gateway event format."""
        mock_main.return_value = {
            "answer": "Test answer",
            "citations": []
        }
        
        event = {"body": json.dumps(mock_lambda_event)}
        result = lambda_handler(event, None)
        
        assert result["statusCode"] == 200
        mock_main.assert_called_once()
    
    @patch('src.rag_lambda.chat_app_lambda.main')
    def test_lambda_handler_error(self, mock_main, mock_lambda_event):
        """Test Lambda handler error handling."""
        mock_main.side_effect = Exception("Test error")
        
        result = lambda_handler(mock_lambda_event, None)
        
        assert result["statusCode"] == 500
        error_body = json.loads(result["body"])
        assert "error" in error_body


class TestMainFunction:
    """Test main function."""
    
    @patch('src.rag_lambda.chat_app_lambda.handle_turn')
    def test_main_with_defaults(self, mock_handle_turn):
        """Test main function with default arguments."""
        mock_handle_turn.return_value = {
            "answer": "Test answer",
            "citations": []
        }
        
        result = main(
            conversation_id="test-conv",
            input_prompt="Test input prompt"
        )
        
        assert result["answer"] == "Test answer"
        mock_handle_turn.assert_called_once_with(
            conversation_id="test-conv",
            input_prompt="Test input prompt",
            retrieval_filters={},
            chat_history_hint=""
        )
    
    @patch('src.rag_lambda.chat_app_lambda.handle_turn')
    def test_main_with_all_args(self, mock_handle_turn):
        """Test main function with all arguments."""
        mock_handle_turn.return_value = {
            "answer": "Test answer",
            "citations": [{"title": "Doc1"}]
        }
        
        result = main(
            conversation_id="test-conv",
            input_prompt="Test input prompt",
            retrieval_filters={"document_type": ["Policy"]},
            user_id="user@example.com",
            chat_history_hint="Previous: Hello"
        )
        
        assert result["answer"] == "Test answer"
        assert len(result["citations"]) == 1
        mock_handle_turn.assert_called_once()


class TestBuildChain:
    """Test chain building function."""
    
    @patch('src.rag_lambda.chat_app_lambda.make_retriever')
    @patch('src.rag_lambda.chat_app_lambda.RunnableWithMessageHistory')
    def test_build_chain(self, mock_history_wrapper, mock_retriever):
        """Test build_chain creates proper chain structure."""
        mock_retriever_instance = Mock()
        mock_retriever.return_value = mock_retriever_instance
        
        chain = build_chain(retrieval_filters={"document_type": ["Policy"]})
        
        mock_retriever.assert_called_once_with({"document_type": ["Policy"]})
        mock_history_wrapper.assert_called_once()


class TestHandleTurn:
    """Test handle_turn function."""
    
    @patch('src.rag_lambda.chat_app_lambda.make_retriever')
    @patch('src.rag_lambda.chat_app_lambda.build_chain')
    @patch('src.rag_lambda.chat_app_lambda.docs_to_citations')
    @patch('src.rag_lambda.chat_app_lambda.docs_to_context')
    def test_handle_turn(self, mock_context, mock_citations, mock_build_chain, mock_retriever):
        """Test handle_turn orchestrates retrieval and chain invocation."""
        # Mock retriever
        mock_retriever_instance = Mock()
        mock_retriever_instance.invoke.return_value = [Mock(metadata={"title": "Doc1"})]
        mock_retriever.return_value = mock_retriever_instance
        
        # Mock chain
        mock_chain_instance = Mock()
        mock_chain_instance.invoke.return_value = Mock(content=[Mock(text="Test answer")])
        mock_build_chain.return_value = mock_chain_instance
        
        # Mock helpers
        mock_context.return_value = "Test context"
        mock_citations.return_value = [{"title": "Doc1"}]
        
        result = handle_turn(
            conversation_id="test-conv",
            input_prompt="Test input prompt",
            retrieval_filters={}
        )
        
        assert result["answer"] == "Test answer"
        assert "citations" in result
        mock_build_chain.assert_called_once()
        mock_chain_instance.invoke.assert_called_once()

