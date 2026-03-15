"""
HTTP HANDLER
═════════════════════════════════════════════════════════════

Handles HTTP request/response processing for video streaming.

Responsibilities:
- Parse HTTP range requests
- Generate HTTP response headers
- Determine content ranges
- Handle HTTP status codes (200, 206, 404, etc.)

Does NOT handle:
- Actual video fetching (that's StreamingClient's job)
- Protocol selection (that's StreamOrchestrator's job)
- Quality selection (that's QualitySelector's job)
- File storage (that's server-side)

Clean separation: HTTP layer vs Streaming layer
"""

from typing import Optional, Tuple, Dict
from dataclasses import dataclass


# ══════════════════════════════════════════════════════════════════════════════
# HTTP RANGE REQUEST
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RangeRequest:
    """Parsed HTTP Range request."""
    start: int
    end: Optional[int]
    total_size: Optional[int] = None
    
    def is_full_file(self) -> bool:
        """Check if requesting full file (no range)."""
        return self.start == 0 and self.end is None
    
    def get_content_length(self) -> int:
        """Calculate content length for this range."""
        if self.end is None:
            if self.total_size is None:
                raise ValueError("Cannot calculate length without end or total_size")
            return self.total_size - self.start
        return self.end - self.start + 1
    
    def __repr__(self) -> str:
        if self.end is None:
            return f"RangeRequest(bytes {self.start}-)"
        return f"RangeRequest(bytes {self.start}-{self.end})"


# ══════════════════════════════════════════════════════════════════════════════
# HTTP RESPONSE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class HTTPResponse:
    """HTTP response configuration."""
    status_code: int
    headers: Dict[str, str]
    media_type: str = "video/mp4"
    
    def __repr__(self) -> str:
        return f"HTTPResponse(status={self.status_code}, headers={len(self.headers)})"


# ══════════════════════════════════════════════════════════════════════════════
# HTTP HANDLER
# ══════════════════════════════════════════════════════════════════════════════

class HTTPHandler:
    """
    Handles HTTP protocol concerns for video streaming.
    
    Separates HTTP layer from streaming layer:
    - Parses Range headers
    - Generates response headers
    - Determines status codes
    - Does NOT fetch data (that's StreamingClient's job)
    
    Example:
        >>> handler = HTTPHandler()
        >>> 
        >>> # Parse range request
        >>> range_req = handler.parse_range_header(
        ...     "bytes=1024-2047",
        ...     file_size=10485760
        ... )
        >>> 
        >>> # Generate response
        >>> response = handler.create_response(
        ...     range_request=range_req,
        ...     file_size=10485760
        ... )
        >>> 
        >>> print(response.status_code)  # 206 Partial Content
        >>> print(response.headers)
    """
    
    def __init__(self):
        """Initialize HTTP handler."""
        pass
    
    # ── Range Request Parsing ────────────────────────────────────────────
    
    def parse_range_header(
        self,
        range_header: Optional[str],
        file_size: int
    ) -> RangeRequest:
        """
        Parse HTTP Range header.
        
        Supports:
        - None or empty → full file (0-)
        - "bytes=0-" → full file
        - "bytes=1024-" → from byte 1024 to end
        - "bytes=1024-2047" → specific range
        - "bytes=-1024" → last 1024 bytes (NOT SUPPORTED YET)
        
        Args:
            range_header: Range header value (e.g., "bytes=1024-2047")
            file_size: Total file size in bytes
            
        Returns:
            RangeRequest object
            
        Example:
            >>> handler = HTTPHandler()
            >>> req = handler.parse_range_header("bytes=1024-2047", 10000)
            >>> print(req.start, req.end)
            1024 2047
        """
        # No range header → full file
        if not range_header or range_header.strip() == "":
            return RangeRequest(start=0, end=None, total_size=file_size)
        
        # Remove "bytes=" prefix
        range_spec = range_header.strip()
        if range_spec.startswith("bytes="):
            range_spec = range_spec[6:]
        
        # Split on hyphen
        parts = range_spec.split("-", 1)
        
        if len(parts) != 2:
            # Invalid format → full file
            return RangeRequest(start=0, end=None, total_size=file_size)
        
        start_str, end_str = parts
        
        # Parse start byte
        try:
            if start_str.strip():
                start = int(start_str)
            else:
                # Suffix range (e.g., "-1024" for last 1024 bytes)
                # NOT SUPPORTED - return full file
                return RangeRequest(start=0, end=None, total_size=file_size)
        except ValueError:
            # Invalid start → full file
            return RangeRequest(start=0, end=None, total_size=file_size)
        
        # Parse end byte
        end = None
        if end_str.strip():
            try:
                end = int(end_str)
            except ValueError:
                # Invalid end → to EOF
                end = None
        
        # Validate and adjust bounds
        start = max(0, start)
        
        if end is not None:
            # Ensure end doesn't exceed file size
            end = min(end, file_size - 1)
            
            # Ensure end >= start
            if end < start:
                end = start
        
        return RangeRequest(start=start, end=end, total_size=file_size)
    
    # ── Response Generation ──────────────────────────────────────────────
    
    def create_response(
        self,
        range_request: RangeRequest,
        file_size: int,
        media_type: str = "video/mp4",
        enable_range_support: bool = True,
        cache_control: str = "no-cache"
    ) -> HTTPResponse:
        """
        Create HTTP response configuration.
        
        Args:
            range_request: Parsed range request
            file_size: Total file size
            media_type: MIME type (default: video/mp4)
            enable_range_support: Enable Accept-Ranges header
            cache_control: Cache-Control header value
            
        Returns:
            HTTPResponse object with status and headers
            
        Example:
            >>> handler = HTTPHandler()
            >>> range_req = RangeRequest(start=1024, end=2047)
            >>> response = handler.create_response(range_req, 10000)
            >>> print(response.status_code)
            206
        """
        headers = {}
        
        # Determine status code
        if range_request.is_full_file():
            # Full file → 200 OK
            status_code = 200
            content_length = file_size
        else:
            # Partial content → 206 Partial Content
            status_code = 206
            content_length = range_request.get_content_length()
            
            # Add Content-Range header
            if range_request.end is None:
                range_end = file_size - 1
            else:
                range_end = range_request.end
            
            headers["Content-Range"] = (
                f"bytes {range_request.start}-{range_end}/{file_size}"
            )
        
        # Common headers
        headers["Content-Length"] = str(content_length)
        headers["Content-Type"] = media_type
        
        if enable_range_support:
            headers["Accept-Ranges"] = "bytes"
        
        if cache_control:
            headers["Cache-Control"] = cache_control
        
        return HTTPResponse(
            status_code=status_code,
            headers=headers,
            media_type=media_type
        )
    
    def create_error_response(
        self,
        status_code: int,
        message: str = ""
    ) -> HTTPResponse:
        """
        Create error response.
        
        Args:
            status_code: HTTP status code (404, 500, etc.)
            message: Optional error message
            
        Returns:
            HTTPResponse for error
            
        Example:
            >>> handler = HTTPHandler()
            >>> response = handler.create_error_response(404, "File not found")
            >>> print(response.status_code)
            404
        """
        headers = {
            "Content-Type": "text/plain",
        }
        
        if message:
            headers["X-Error-Message"] = message
        
        return HTTPResponse(
            status_code=status_code,
            headers=headers,
            media_type="text/plain"
        )
    
    # ── Helper Methods ───────────────────────────────────────────────────
    
    def get_status_message(self, status_code: int) -> str:
        """
        Get human-readable status message.
        
        Args:
            status_code: HTTP status code
            
        Returns:
            Status message string
            
        Example:
            >>> handler = HTTPHandler()
            >>> handler.get_status_message(206)
            'Partial Content'
        """
        messages = {
            200: "OK",
            206: "Partial Content",
            400: "Bad Request",
            404: "Not Found",
            416: "Range Not Satisfiable",
            500: "Internal Server Error",
            503: "Service Unavailable",
        }
        return messages.get(status_code, "Unknown")
    
    def is_range_request(self, range_header: Optional[str]) -> bool:
        """
        Check if request includes a Range header.
        
        Args:
            range_header: Range header value
            
        Returns:
            True if range request
            
        Example:
            >>> handler = HTTPHandler()
            >>> handler.is_range_request("bytes=0-1023")
            True
            >>> handler.is_range_request(None)
            False
        """
        if not range_header:
            return False
        
        range_spec = range_header.strip()
        if not range_spec or range_spec == "bytes=0-":
            return False
        
        return range_spec.startswith("bytes=")
    
    def validate_range(
        self,
        range_request: RangeRequest,
        file_size: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate range request against file size.
        
        Args:
            range_request: Parsed range request
            file_size: Actual file size
            
        Returns:
            Tuple of (is_valid, error_message)
            
        Example:
            >>> handler = HTTPHandler()
            >>> req = RangeRequest(start=5000, end=10000)
            >>> valid, error = handler.validate_range(req, 8000)
            >>> print(valid, error)
            False "Range exceeds file size"
        """
        # Check if start is beyond file size
        if range_request.start >= file_size:
            return False, f"Start byte {range_request.start} exceeds file size {file_size}"
        
        # Check if end exceeds file size
        if range_request.end is not None and range_request.end >= file_size:
            return False, f"End byte {range_request.end} exceeds file size {file_size}"
        
        return True, None


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running HTTPHandler self-tests...\n")
    
    handler = HTTPHandler()
    
    # Test 1: Parse full file request (no range)
    req = handler.parse_range_header(None, file_size=10000)
    assert req.start == 0
    assert req.end is None
    assert req.is_full_file() == True
    print("✓ Parse full file request (no range)")
    
    # Test 2: Parse full file request (bytes=0-)
    req = handler.parse_range_header("bytes=0-", file_size=10000)
    assert req.start == 0
    assert req.end is None
    print("✓ Parse full file request (bytes=0-)")
    
    # Test 3: Parse range from offset
    req = handler.parse_range_header("bytes=1024-", file_size=10000)
    assert req.start == 1024
    assert req.end is None
    print("✓ Parse range from offset (bytes=1024-)")
    
    # Test 4: Parse specific range
    req = handler.parse_range_header("bytes=1024-2047", file_size=10000)
    assert req.start == 1024
    assert req.end == 2047
    assert req.get_content_length() == 1024
    print("✓ Parse specific range (bytes=1024-2047)")
    
    # Test 5: Create 200 OK response
    req = RangeRequest(start=0, end=None, total_size=10000)
    response = handler.create_response(req, file_size=10000)
    assert response.status_code == 200
    assert response.headers["Content-Length"] == "10000"
    assert "Content-Range" not in response.headers
    print("✓ Create 200 OK response")
    
    # Test 6: Create 206 Partial Content response
    req = RangeRequest(start=1024, end=2047)
    response = handler.create_response(req, file_size=10000)
    assert response.status_code == 206
    assert response.headers["Content-Length"] == "1024"
    assert response.headers["Content-Range"] == "bytes 1024-2047/10000"
    print("✓ Create 206 Partial Content response")
    
    # Test 7: Create error response
    response = handler.create_error_response(404, "File not found")
    assert response.status_code == 404
    assert response.headers["Content-Type"] == "text/plain"
    print("✓ Create error response")
    
    # Test 8: Get status message
    assert handler.get_status_message(200) == "OK"
    assert handler.get_status_message(206) == "Partial Content"
    assert handler.get_status_message(404) == "Not Found"
    print("✓ Get status message")
    
    # Test 9: Is range request check
    assert handler.is_range_request("bytes=1024-2047") == True
    assert handler.is_range_request("bytes=0-") == False
    assert handler.is_range_request(None) == False
    print("✓ Is range request check")
    
    # Test 10: Validate range
    req = RangeRequest(start=1024, end=2047)
    valid, error = handler.validate_range(req, file_size=10000)
    assert valid == True
    assert error is None
    print("✓ Validate valid range")
    
    # Test 11: Validate invalid range (exceeds file size)
    req = RangeRequest(start=15000, end=20000)
    valid, error = handler.validate_range(req, file_size=10000)
    assert valid == False
    assert "exceeds" in error
    print("✓ Validate invalid range (exceeds file size)")
    
    # Test 12: Range bounds adjustment
    req = handler.parse_range_header("bytes=5000-15000", file_size=10000)
    assert req.start == 5000
    assert req.end == 9999  # Adjusted to file_size - 1
    print("✓ Range bounds adjustment")
    
    print("\n✅ All HTTPHandler tests passed!")
    print("\nExample usage:")
    print("  handler = HTTPHandler()")
    print("  ")
    print("  # Parse range from request")
    print("  range_req = handler.parse_range_header(")
    print("      request.headers.get('Range'),")
    print("      file_size=file_size")
    print("  )")
    print("  ")
    print("  # Create response headers")
    print("  response = handler.create_response(")
    print("      range_request=range_req,")
    print("      file_size=file_size")
    print("  )")
    print("  ")
    print("  # Use in FastAPI")
    print("  return StreamingResponse(")
    print("      generator,")
    print("      status_code=response.status_code,")
    print("      media_type=response.media_type,")
    print("      headers=response.headers")
    print("  )")
