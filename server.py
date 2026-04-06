#!/usr/bin/env python3
"""
MCP Server for ArmSoft SME Trade Public API.

Provides tools for managing products, partners, documents, reports, and journals
in the ArmSoft Trade ERP system. Designed for use with n8n and other workflow
automation platforms via HTTP/SSE transport.

Authentication:
    Set the ARMSOFT_API_KEY environment variable with your Full Access API key.
    Optionally set ARMSOFT_BASE_URL to override the default API endpoint.
    Optionally set ARMSOFT_LANGUAGE to set the response language (default: en-US).

Usage:
    pip install -r requirements.txt
    ARMSOFT_API_KEY=your-key python server.py
"""

import json
import os
from typing import Optional, List, Any, Dict
from enum import Enum

import httpx
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

mcp = FastMCP("armsoft_trade_mcp")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_BASE_URL = os.getenv("ARMSOFT_BASE_URL", "https://api.armsoft.am/trade/v1")
API_KEY = os.getenv("ARMSOFT_API_KEY", "")
LANGUAGE = os.getenv("ARMSOFT_LANGUAGE", "en-US")
CHARACTER_LIMIT = 25_000
REQUEST_TIMEOUT = 30.0

# ---------------------------------------------------------------------------
# Shared HTTP utilities
# ---------------------------------------------------------------------------

def _headers() -> Dict[str, str]:
    """Return standard request headers including auth and language."""
    return {
        "apiKey": API_KEY,
        "Accept-Language": LANGUAGE,
        "Content-Type": "application/json",
        "accept": "application/json",
    }


async def _get(path: str) -> Any:
    """Perform a GET request and return parsed JSON."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE_URL}{path}", headers=_headers())
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()


async def _post(path: str, body: dict) -> Any:
    """Perform a POST request and return parsed JSON."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(
            f"{API_BASE_URL}{path}", headers=_headers(), json=body
        )
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()


async def _put(path: str, body: dict) -> Any:
    """Perform a PUT request and return parsed JSON."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.put(
            f"{API_BASE_URL}{path}", headers=_headers(), json=body
        )
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()


async def _delete(path: str) -> Any:
    """Perform a DELETE request and return parsed JSON."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.delete(f"{API_BASE_URL}{path}", headers=_headers())
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()


async def _fetch_all_pages(initial_path: str, nextpage_path: str, body: dict) -> List[Any]:
    """
    Fetch all pages of a paginated list endpoint automatically.

    Sends the initial POST request, then follows /nextpage until hasMore is False.
    Returns a flat list of all data rows combined across all pages.
    """
    body = {**body, "pageSize": 5000}
    result = await _post(initial_path, body)

    # If response is not paginated (no id field), return data directly
    if not isinstance(result, dict) or "data" not in result:
        return result if isinstance(result, list) else [result]

    all_data: List[Any] = list(result.get("data", []))
    pagination_id = result.get("id")
    has_more = result.get("hasMore", False)

    while has_more and pagination_id:
        page = await _post(nextpage_path, {"id": pagination_id, "close": False})
        all_data.extend(page.get("data", []))
        has_more = page.get("hasMore", False)
        pagination_id = page.get("id", pagination_id)

    return all_data


def _handle_error(e: Exception) -> str:
    """Return a clear, actionable error string from an exception."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        try:
            msg = e.response.json().get("message", "")
        except Exception:
            msg = e.response.text or ""

        if status == 401:
            if not API_KEY:
                return "Error: No API key set. Set the ARMSOFT_API_KEY environment variable."
            return f"Error: Authentication failed. {msg or 'Check your API key.'}"
        if status == 400:
            return f"Error: Bad request — {msg or 'check required fields and formats.'}. Fix the input and try again."
        if status == 404:
            return "Error: Resource not found. Verify the code or ISN is correct."
        if status == 409:
            return f"Error: Conflict — {msg or 'resource may already exist or is in use.'}."
        if status == 429:
            return "Error: Rate limit exceeded. Wait a moment and retry."
        if status == 500:
            return "Error: ArmSoft server error. Try again later or contact support."
        return f"Error: API returned status {status}. {msg}"
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. The API may be slow — try again."
    if isinstance(e, httpx.ConnectError):
        return "Error: Could not connect to the API. Check your network and ARMSOFT_BASE_URL."
    return f"Error: {type(e).__name__}: {e}"


def _truncate(data: Any, result_key: str = "data") -> dict:
    """
    Wrap data in a response envelope, truncating if the JSON would exceed CHARACTER_LIMIT.

    Returns a dict ready to be json.dumps'd.
    """
    items = data if isinstance(data, list) else [data]
    envelope = {result_key: items, "count": len(items), "truncated": False}
    raw = json.dumps(envelope, ensure_ascii=False)

    if len(raw) > CHARACTER_LIMIT:
        # Reduce until it fits
        keep = max(1, len(items) * CHARACTER_LIMIT // len(raw))
        envelope[result_key] = items[:keep]
        envelope["count"] = keep
        envelope["truncated"] = True
        envelope["truncation_note"] = (
            f"Response truncated to {keep} of {len(items)} items. "
            "Use filters (codes, group, lastModifiedDate, pageSize) to narrow your query."
        )

    return envelope


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ShowMode(str, Enum):
    ALL = "3"
    IN_PRICE_LIST = "1"
    NOT_IN_PRICE_LIST = "2"


class ItemType(str, Enum):
    PRODUCTS = "1"
    SERVICES = "2"


# ===========================================================================
# TOOLS — PRODUCTS
# ===========================================================================

class ListProductsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    show_mode: ShowMode = Field(
        default=ShowMode.ALL,
        description="Which products to return: '3'=all (default), '1'=in price list only, '2'=not in price list",
    )
    group: Optional[str] = Field(
        default=None, description="Filter by product group code (e.g., '110')"
    )
    item_type: Optional[ItemType] = Field(
        default=None, description="Filter by type: '1'=Products, '2'=Services"
    )
    codes: Optional[List[str]] = Field(
        default=None, description="Fetch specific product codes (e.g., ['1001', '1002'])"
    )
    extended: bool = Field(
        default=False, description="When true, returns full product details including prices and all metadata"
    )
    price_list_types: Optional[List[str]] = Field(
        default=None, description="Include prices for these price list type codes (e.g., ['01', '02'])"
    )
    last_modified_date: Optional[str] = Field(
        default=None, description="Only return products modified after this date (ISO 8601, e.g., '2026-01-01T00:00:00Z')"
    )


@mcp.tool(
    name="trade_list_products",
    annotations={
        "title": "List Products",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_list_products(params: ListProductsInput) -> str:
    """
    List and filter products from the ArmSoft Trade product catalog.

    Automatically fetches all pages and returns them as a single list.
    Use filters to narrow results — the full catalog can be large.

    Args:
        params (ListProductsInput):
            - show_mode: '3'=all (default), '1'=in price list, '2'=not in price list
            - group: product group code filter
            - item_type: '1'=Products, '2'=Services
            - codes: specific product codes to fetch
            - extended: return full product detail including prices
            - price_list_types: include price columns for these price list types
            - last_modified_date: only return records changed after this date

    Returns:
        str: JSON with { "data": [...products], "count": int, "truncated": bool }

    Examples:
        - Sync catalog to webstore: extended=true, price_list_types=["01"]
        - Get changed products since last sync: last_modified_date="2026-04-01T00:00:00Z"
        - Get specific items: codes=["1001", "1002"]
    """
    try:
        body: dict = {"showMode": params.show_mode.value, "extended": params.extended}
        if params.group:
            body["group"] = params.group
        if params.item_type:
            body["type"] = params.item_type.value
        if params.codes:
            body["codes"] = params.codes
        if params.price_list_types:
            body["priceListTypes"] = params.price_list_types
        if params.last_modified_date:
            body["lastModifiedDate"] = params.last_modified_date

        all_items = await _fetch_all_pages(
            "/directories/products/list",
            "/directories/products/list/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class GetProductInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    code: str = Field(..., description="Product code (e.g., '1001')", min_length=1)


@mcp.tool(
    name="trade_get_product",
    annotations={
        "title": "Get Product",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_product(params: GetProductInput) -> str:
    """
    Get a single product by its code.

    Args:
        params (GetProductInput):
            - code: product code (e.g., '1001')

    Returns:
        str: JSON product object or error string

    Examples:
        - Look up product details before creating a sale line: code="1001"
    """
    try:
        data = await _get(f"/directories/products/{params.code}")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class CreateProductInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: Optional[str] = Field(
        default=None,
        description="Product code. Leave empty for auto-generated code.",
    )
    name: str = Field(..., description="Product name", min_length=1, max_length=500)
    group: Optional[str] = Field(default=None, description="Product group code")
    item_type: ItemType = Field(
        default=ItemType.PRODUCTS, description="'1'=Product (default), '2'=Service"
    )
    base_unit_measure: Optional[str] = Field(
        default=None, description="Base unit of measure code (e.g., '001')"
    )
    vat: bool = Field(default=True, description="Whether this product is subject to VAT")
    barcode: Optional[str] = Field(default=None, description="Product barcode")
    specification: Optional[str] = Field(default=None, description="Product specification / description")
    external_code: Optional[str] = Field(default=None, description="External system code for cross-reference")
    extra_fields: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Any additional Product fields to pass directly to the API (e.g., {'addedValuePercent': 20})",
    )


@mcp.tool(
    name="trade_create_product",
    annotations={
        "title": "Create Product",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trade_create_product(params: CreateProductInput) -> str:
    """
    Create a new product in the ArmSoft Trade catalog.

    If code is omitted, the server auto-generates the next available code.

    Args:
        params (CreateProductInput):
            - code: optional product code (auto-generated if empty)
            - name: product display name (required)
            - group: product group code
            - item_type: '1'=Product, '2'=Service
            - base_unit_measure: unit of measure code
            - vat: subject to VAT (default true)
            - barcode: product barcode
            - specification: detailed description
            - external_code: code in an external system
            - extra_fields: dict of any additional API fields

    Returns:
        str: JSON of the created product object including its assigned code

    Error Handling:
        - 409 Conflict: product code already exists — use a different code
        - 400 Bad Request: missing required fields or invalid values
    """
    try:
        body: dict = {
            "name": params.name,
            "type": params.item_type.value,
            "vat": params.vat,
        }
        if params.code:
            body["code"] = params.code
        if params.group:
            body["group"] = params.group
        if params.base_unit_measure:
            body["baseUnitMeasure"] = params.base_unit_measure
        if params.barcode:
            body["barcode"] = params.barcode
        if params.specification:
            body["specification"] = params.specification
        if params.external_code:
            body["externalCode"] = params.external_code
        if params.extra_fields:
            body.update(params.extra_fields)

        data = await _post("/directories/products", body)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class UpdateProductInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(..., description="Product code to update (e.g., '1001')", min_length=1)
    fields: Dict[str, Any] = Field(
        ...,
        description=(
            "Fields to update as a dict. Use the same field names as returned by trade_get_product. "
            "Example: {'name': 'New Name', 'vat': false, 'specification': 'Updated spec'}"
        ),
    )


@mcp.tool(
    name="trade_update_product",
    annotations={
        "title": "Update Product",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_update_product(params: UpdateProductInput) -> str:
    """
    Update an existing product by its code.

    First fetch the product with trade_get_product, then pass the full
    updated object (or just changed fields) via the 'fields' parameter.

    Args:
        params (UpdateProductInput):
            - code: product code to update
            - fields: dict of fields to update (e.g., {'name': 'New Name', 'vat': false})

    Returns:
        str: JSON of the updated product object

    Error Handling:
        - 404: product not found — verify the code
        - 400: invalid field values — check field types and constraints
    """
    try:
        data = await _put(f"/directories/products/{params.code}", params.fields)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# TOOLS — PARTNERS
# ===========================================================================

class ListPartnersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    codes: Optional[List[str]] = Field(
        default=None, description="Filter by specific partner codes (e.g., ['P001', 'P002'])"
    )
    group: Optional[str] = Field(
        default=None, description="Filter by partner group code"
    )
    extended: bool = Field(
        default=False, description="When true, returns full partner details including contacts and addresses"
    )
    last_modified_date: Optional[str] = Field(
        default=None, description="Only return partners modified after this date (ISO 8601, e.g., '2026-01-01T00:00:00Z')"
    )


@mcp.tool(
    name="trade_list_partners",
    annotations={
        "title": "List Partners",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_list_partners(params: ListPartnersInput) -> str:
    """
    List and filter partners (customers and suppliers) from ArmSoft Trade.

    Automatically fetches all pages.

    Args:
        params (ListPartnersInput):
            - codes: specific partner codes to fetch
            - group: partner group code filter
            - extended: return full details including contacts
            - last_modified_date: only return records changed after this date

    Returns:
        str: JSON with { "data": [...partners], "count": int, "truncated": bool }

    Examples:
        - Sync all customers to CRM: extended=true
        - Get changed partners: last_modified_date="2026-04-01T00:00:00Z"
    """
    try:
        body: dict = {"extended": params.extended}
        if params.codes:
            body["codes"] = params.codes
        if params.group:
            body["group"] = params.group
        if params.last_modified_date:
            body["lastModifiedDate"] = params.last_modified_date

        all_items = await _fetch_all_pages(
            "/directories/partners/list",
            "/directories/partners/list/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class GetPartnerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    code: str = Field(..., description="Partner code (e.g., 'P001')", min_length=1)


@mcp.tool(
    name="trade_get_partner",
    annotations={
        "title": "Get Partner",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_partner(params: GetPartnerInput) -> str:
    """
    Get a single partner by code.

    Args:
        params (GetPartnerInput):
            - code: partner code (e.g., 'P001')

    Returns:
        str: JSON partner object with name, taxCode, supplier/customer flags, etc.
    """
    try:
        data = await _get(f"/directories/partners/{params.code}")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class CreatePartnerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: Optional[str] = Field(
        default=None, description="Partner code. Leave empty for auto-generated code."
    )
    name: str = Field(..., description="Partner display name", min_length=1, max_length=500)
    full_name: Optional[str] = Field(default=None, description="Full legal name")
    tax_code: Optional[str] = Field(default=None, description="Tax identification number")
    group: Optional[str] = Field(default=None, description="Partner group code")
    is_supplier: bool = Field(default=False, description="Mark as a supplier")
    is_customer: bool = Field(default=True, description="Mark as a customer (default true)")
    extra_fields: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional Partner fields to pass to the API (e.g., {'address': '...'})",
    )


@mcp.tool(
    name="trade_create_partner",
    annotations={
        "title": "Create Partner",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trade_create_partner(params: CreatePartnerInput) -> str:
    """
    Create a new partner (customer or supplier) in ArmSoft Trade.

    Args:
        params (CreatePartnerInput):
            - code: optional partner code (auto-generated if empty)
            - name: partner display name (required)
            - full_name: legal company name
            - tax_code: tax ID number
            - group: partner group code
            - is_supplier: mark as supplier (default false)
            - is_customer: mark as customer (default true)
            - extra_fields: additional API fields

    Returns:
        str: JSON of the created partner object

    Error Handling:
        - 409 Conflict: partner code already exists
    """
    try:
        body: dict = {
            "name": params.name,
            "supplier": params.is_supplier,
            "customer": params.is_customer,
        }
        if params.code:
            body["code"] = params.code
        if params.full_name:
            body["fullName"] = params.full_name
        if params.tax_code:
            body["taxCode"] = params.tax_code
        if params.group:
            body["group"] = params.group
        if params.extra_fields:
            body.update(params.extra_fields)

        data = await _post("/directories/partners", body)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# TOOLS — DOCUMENTS
# ===========================================================================

class SaleLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_code: str = Field(..., description="Product code (e.g., '1001')", min_length=1)
    quantity: float = Field(..., description="Quantity to sell", gt=0)
    price: float = Field(..., description="Unit price", ge=0)
    unit_measure: Optional[str] = Field(default=None, description="Unit of measure code")
    discount_percent: Optional[float] = Field(default=None, description="Line discount %", ge=0, le=100)


class CreateSaleInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_date: str = Field(
        ..., description="Sale date in YYYY-MM-DD format (e.g., '2026-04-06')"
    )
    partner: str = Field(..., description="Partner (customer) code (e.g., 'P100')", min_length=1)
    lines: List[SaleLineInput] = Field(
        ..., description="Sale line items — at least one required", min_length=1
    )
    storage: Optional[str] = Field(default=None, description="Source storage code")
    cashdesk: Optional[str] = Field(default=None, description="Cashdesk code for payment")
    note: Optional[str] = Field(default=None, description="Internal note or comment")
    extra_fields: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional sale document fields"
    )


@mcp.tool(
    name="trade_create_sale",
    annotations={
        "title": "Create Sale",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trade_create_sale(params: CreateSaleInput) -> str:
    """
    Create a new sale document in ArmSoft Trade.

    Use this to record a sale from an external order system, webstore, or n8n workflow.

    Args:
        params (CreateSaleInput):
            - document_date: date of sale in YYYY-MM-DD format (required)
            - partner: customer partner code (required)
            - lines: list of line items, each with item_code, quantity, price (required)
            - storage: source storage/warehouse code
            - cashdesk: cashdesk code
            - note: optional internal note
            - extra_fields: additional API-level fields

    Returns:
        str: JSON of the created sale with its ISN (unique document ID)

    Error Handling:
        - 400: missing required fields or invalid partner/product codes
        - 404: partner or product not found — verify codes with trade_get_partner / trade_get_product
    """
    try:
        lines = [
            {
                "itemCode": ln.item_code,
                "quantity": ln.quantity,
                "price": ln.price,
                **({"unitMeasure": ln.unit_measure} if ln.unit_measure else {}),
                **({"discountPercent": ln.discount_percent} if ln.discount_percent is not None else {}),
            }
            for ln in params.lines
        ]

        body: dict = {
            "documentDate": params.document_date,
            "partner": params.partner,
            "lines": lines,
        }
        if params.storage:
            body["storage"] = params.storage
        if params.cashdesk:
            body["cashdesk"] = params.cashdesk
        if params.note:
            body["note"] = params.note
        if params.extra_fields:
            body.update(params.extra_fields)

        data = await _post("/documents/sale", body)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class GetDocumentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    isn: str = Field(
        ...,
        description="Document ISN (unique identifier returned when creating a document, e.g., '550e8400-e29b-41d4-a716-446655440004')",
        min_length=1,
    )


@mcp.tool(
    name="trade_get_sale",
    annotations={
        "title": "Get Sale",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_sale(params: GetDocumentInput) -> str:
    """
    Retrieve a sale document by its ISN.

    Args:
        params (GetDocumentInput):
            - isn: the sale document ISN (returned by trade_create_sale)

    Returns:
        str: JSON sale document including partner, date, line items, and totals
    """
    try:
        data = await _get(f"/documents/sale/{params.isn}")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class InvoiceLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_code: str = Field(..., description="Product code", min_length=1)
    quantity: float = Field(..., description="Quantity", gt=0)
    price: float = Field(..., description="Unit price", ge=0)
    unit_measure: Optional[str] = Field(default=None, description="Unit of measure code")


class CreateInvoiceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_date: str = Field(
        ..., description="Invoice date in YYYY-MM-DD format (e.g., '2026-04-06')"
    )
    partner: str = Field(..., description="Customer/partner code", min_length=1)
    lines: List[InvoiceLineInput] = Field(
        ..., description="Invoice line items — at least one required", min_length=1
    )
    storage: Optional[str] = Field(default=None, description="Source storage code")
    note: Optional[str] = Field(default=None, description="Invoice note or comment")
    extra_fields: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional invoice fields"
    )


@mcp.tool(
    name="trade_create_invoice",
    annotations={
        "title": "Create Invoice",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trade_create_invoice(params: CreateInvoiceInput) -> str:
    """
    Create a new invoice document in ArmSoft Trade.

    Args:
        params (CreateInvoiceInput):
            - document_date: date in YYYY-MM-DD format (required)
            - partner: partner/customer code (required)
            - lines: list of line items with item_code, quantity, price (required)
            - storage: source storage code
            - note: optional note
            - extra_fields: additional API fields

    Returns:
        str: JSON of created invoice with ISN, document number, and line totals
    """
    try:
        lines = [
            {
                "itemCode": ln.item_code,
                "quantity": ln.quantity,
                "price": ln.price,
                **({"unitMeasure": ln.unit_measure} if ln.unit_measure else {}),
            }
            for ln in params.lines
        ]

        body: dict = {
            "documentDate": params.document_date,
            "partner": params.partner,
            "lines": lines,
        }
        if params.storage:
            body["storage"] = params.storage
        if params.note:
            body["note"] = params.note
        if params.extra_fields:
            body.update(params.extra_fields)

        data = await _post("/documents/invoice", body)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="trade_get_invoice",
    annotations={
        "title": "Get Invoice",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_invoice(params: GetDocumentInput) -> str:
    """
    Retrieve an invoice document by its ISN.

    Args:
        params (GetDocumentInput):
            - isn: the invoice document ISN (returned by trade_create_invoice)

    Returns:
        str: JSON invoice document including partner, date, lines, and document number
    """
    try:
        data = await _get(f"/documents/invoice/{params.isn}")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# TOOLS — REPORTS
# ===========================================================================

class ProductsBalancesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    date: str = Field(
        ..., description="Balance date in YYYY-MM-DD format (e.g., '2026-04-06')"
    )
    storages: Optional[List[str]] = Field(
        default=None, description="Filter by storage codes (e.g., ['S001', 'S002']). Omit for all storages."
    )
    product_codes: Optional[List[str]] = Field(
        default=None, description="Filter by product codes. Omit for all products."
    )


@mcp.tool(
    name="trade_get_products_balances",
    annotations={
        "title": "Get Products Balances (Stock Levels)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_products_balances(params: ProductsBalancesInput) -> str:
    """
    Get product stock/inventory balances for a given date.

    Use to check available stock before creating a sale, or to sync inventory
    levels to an external system on a schedule.

    Args:
        params (ProductsBalancesInput):
            - date: balance date in YYYY-MM-DD format (required)
            - storages: list of storage codes to filter (omit for all)
            - product_codes: list of product codes to filter (omit for all)

    Returns:
        str: JSON with { "data": [...balance rows], "count": int }
        Each row contains product code, name, quantity, and storage info.

    Examples:
        - Daily stock sync: date=today, storages=["MAIN"]
        - Check specific items: date="2026-04-06", product_codes=["1001","1002"]
    """
    try:
        body: dict = {"date": params.date}
        if params.storages:
            body["storages"] = params.storages
        if params.product_codes:
            body["itemCodes"] = params.product_codes

        all_items = await _fetch_all_pages(
            "/reports/productsbalances",
            "/reports/productsbalances/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class PriceListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    date: str = Field(
        ..., description="Price list date in YYYY-MM-DD format (e.g., '2026-04-06')"
    )
    price_list_types: Optional[List[str]] = Field(
        default=None, description="Price list type codes to include (e.g., ['01', '02']). Omit for all."
    )
    items_show_mode: ShowMode = Field(
        default=ShowMode.ALL,
        description="'3'=all products (default), '1'=in price list only, '2'=not in price list",
    )
    product_codes: Optional[List[str]] = Field(
        default=None, description="Filter by specific product codes"
    )


@mcp.tool(
    name="trade_get_price_list",
    annotations={
        "title": "Get Price List",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_price_list(params: PriceListInput) -> str:
    """
    Get the price list for a specific date and price list type(s).

    Use for syncing prices to a webstore, displaying current prices, or
    checking pricing before creating documents.

    Args:
        params (PriceListInput):
            - date: price list date in YYYY-MM-DD (required)
            - price_list_types: price list type codes (e.g., ['01']) — omit for all
            - items_show_mode: '3'=all, '1'=in price list, '2'=not in price list
            - product_codes: filter to specific products

    Returns:
        str: JSON with { "data": [...price rows], "count": int }
        Each row contains product code, name, price, and price list type.
    """
    try:
        body: dict = {"itemsShowMode": params.items_show_mode.value}
        if params.date:
            body["date"] = params.date
        if params.price_list_types:
            body["priceListTypes"] = params.price_list_types
        if params.product_codes:
            body["itemCodes"] = params.product_codes

        all_items = await _fetch_all_pages(
            "/reports/pricelist",
            "/reports/pricelist/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class SalesAnalysisInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    start_date: str = Field(
        ..., description="Start date in YYYY-MM-DD format (e.g., '2026-01-01')"
    )
    end_date: str = Field(
        ..., description="End date in YYYY-MM-DD format (e.g., '2026-04-06')"
    )
    partner_code: Optional[str] = Field(
        default=None, description="Filter by partner code"
    )
    product_code: Optional[str] = Field(
        default=None, description="Filter by product code"
    )
    storage: Optional[str] = Field(
        default=None, description="Filter by storage code"
    )


@mcp.tool(
    name="trade_get_sales_analysis",
    annotations={
        "title": "Get Sales Analysis",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_sales_analysis(params: SalesAnalysisInput) -> str:
    """
    Get sales analysis data for a date range.

    Returns detailed sales breakdown by product, partner, and storage.
    Use for reporting, dashboards, or periodic data exports to spreadsheets.

    Args:
        params (SalesAnalysisInput):
            - start_date: period start in YYYY-MM-DD (required)
            - end_date: period end in YYYY-MM-DD (required)
            - partner_code: filter to one partner
            - product_code: filter to one product
            - storage: filter to one storage

    Returns:
        str: JSON with { "data": [...sales rows], "count": int }

    Examples:
        - Monthly sales report: start_date="2026-03-01", end_date="2026-03-31"
        - Sales for one customer: start_date=..., end_date=..., partner_code="P100"
    """
    try:
        body: dict = {"startDate": params.start_date, "endDate": params.end_date}
        if params.partner_code:
            body["partnerCode"] = params.partner_code
        if params.product_code:
            body["itemCode"] = params.product_code
        if params.storage:
            body["storage"] = params.storage

        all_items = await _fetch_all_pages(
            "/reports/salesanalysis",
            "/reports/salesanalysis/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class PartnersBalancesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    date: str = Field(
        ..., description="Balance date in YYYY-MM-DD format (e.g., '2026-04-06')"
    )
    currency: Optional[str] = Field(
        default="AMD", description="Report currency code (e.g., 'AMD', 'USD')"
    )
    partner_code: Optional[str] = Field(
        default=None, description="Filter to a specific partner"
    )


@mcp.tool(
    name="trade_get_partners_balances",
    annotations={
        "title": "Get Partners Balances",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_partners_balances(params: PartnersBalancesInput) -> str:
    """
    Get partner account balances (receivables / payables) for a given date.

    Use for accounts-receivable monitoring, debt tracking, or partner statements.

    Args:
        params (PartnersBalancesInput):
            - date: balance date in YYYY-MM-DD (required)
            - currency: reporting currency code (default 'AMD')
            - partner_code: filter to one specific partner

    Returns:
        str: JSON with { "data": [...balance rows], "count": int }
        Each row shows partner name, debit, credit, and net balance.
    """
    try:
        body: dict = {"date": params.date, "reportCurrency": params.currency or "AMD"}
        if params.partner_code:
            body["partnerCode"] = params.partner_code

        all_items = await _fetch_all_pages(
            "/reports/partnersbalances",
            "/reports/partnersbalances/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# TOOLS — JOURNAL
# ===========================================================================

class DocumentsJournalInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    start_date: str = Field(
        ..., description="Journal start date in YYYY-MM-DD format (e.g., '2026-01-01')"
    )
    end_date: str = Field(
        ..., description="Journal end date in YYYY-MM-DD format (e.g., '2026-04-06')"
    )
    partner_code: Optional[str] = Field(
        default=None, description="Filter by partner code"
    )
    product_code: Optional[str] = Field(
        default=None, description="Filter by product code"
    )
    document_types: Optional[List[str]] = Field(
        default=None, description="Filter by document type codes"
    )
    currency: Optional[str] = Field(
        default=None, description="Filter by currency code (e.g., 'AMD', 'USD')"
    )


@mcp.tool(
    name="trade_get_documents_journal",
    annotations={
        "title": "Get Documents Journal",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_documents_journal(params: DocumentsJournalInput) -> str:
    """
    Get the all-documents journal for a date range.

    Returns a log of all document transactions (sales, invoices, returns, etc.)
    within the specified period. Useful for auditing, syncing, or reporting.

    Args:
        params (DocumentsJournalInput):
            - start_date: period start in YYYY-MM-DD (required)
            - end_date: period end in YYYY-MM-DD (required)
            - partner_code: filter to one partner
            - product_code: filter to one product
            - document_types: filter by document type codes
            - currency: filter by currency code

    Returns:
        str: JSON with { "data": [...journal rows], "count": int }
        Each row contains document type, date, partner, amount, and ISN.

    Examples:
        - Daily sync of new documents: start_date=yesterday, end_date=today
        - Partner statement: start_date="2026-01-01", end_date="2026-04-06", partner_code="P100"
    """
    try:
        body: dict = {"startDate": params.start_date, "endDate": params.end_date}
        if params.partner_code:
            body["partnerCode"] = params.partner_code
        if params.product_code:
            body["itemCode"] = params.product_code
        if params.document_types:
            body["documentTypes"] = params.document_types
        if params.currency:
            body["currency"] = params.currency

        all_items = await _fetch_all_pages(
            "/journals/alldocuments",
            "/journals/alldocuments/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# TOOLS — DOCUMENTS (continued)
# ===========================================================================

# ── Sale Return ─────────────────────────────────────────────────────────────

class CreateSaleReturnInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_date: str = Field(..., description="Return date in YYYY-MM-DD format")
    partner: str = Field(..., description="Partner (customer) code being returned from", min_length=1)
    lines: List[SaleLineInput] = Field(..., description="Items being returned — at least one required", min_length=1)
    storage: Optional[str] = Field(default=None, description="Storage to return goods into")
    note: Optional[str] = Field(default=None, description="Reason for return or internal note")
    extra_fields: Optional[Dict[str, Any]] = Field(default=None, description="Additional API fields")


@mcp.tool(
    name="trade_create_sale_return",
    annotations={
        "title": "Create Sale Return",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trade_create_sale_return(params: CreateSaleReturnInput) -> str:
    """
    Create a sale return document — used when a customer returns goods.

    Args:
        params (CreateSaleReturnInput):
            - document_date: date of return in YYYY-MM-DD (required)
            - partner: customer partner code (required)
            - lines: items being returned with quantity and price (required)
            - storage: storage to receive the returned goods
            - note: reason for return or internal note
            - extra_fields: additional API fields

    Returns:
        str: JSON of the created sale return document with its ISN
    """
    try:
        lines = [
            {
                "itemCode": ln.item_code,
                "quantity": ln.quantity,
                "price": ln.price,
                **({"unitMeasure": ln.unit_measure} if ln.unit_measure else {}),
                **({"discountPercent": ln.discount_percent} if ln.discount_percent is not None else {}),
            }
            for ln in params.lines
        ]
        body: dict = {
            "documentDate": params.document_date,
            "partner": params.partner,
            "lines": lines,
        }
        if params.storage:
            body["storage"] = params.storage
        if params.note:
            body["note"] = params.note
        if params.extra_fields:
            body.update(params.extra_fields)

        data = await _post("/documents/salereturn", body)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="trade_get_sale_return",
    annotations={
        "title": "Get Sale Return",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_sale_return(params: GetDocumentInput) -> str:
    """
    Retrieve a sale return document by its ISN.

    Args:
        params (GetDocumentInput):
            - isn: the sale return document ISN

    Returns:
        str: JSON sale return document with partner, date, and returned line items
    """
    try:
        data = await _get(f"/documents/salereturn/{params.isn}")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ── Storage Input Order ──────────────────────────────────────────────────────

class StorageInputLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_code: str = Field(..., description="Product code", min_length=1)
    quantity: float = Field(..., description="Quantity to receive into storage", gt=0)
    price: float = Field(..., description="Unit cost/price", ge=0)
    unit_measure: Optional[str] = Field(default=None, description="Unit of measure code")


class CreateStorageInputOrderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_date: str = Field(..., description="Date in YYYY-MM-DD format")
    storage: str = Field(..., description="Destination storage code to receive goods into", min_length=1)
    lines: List[StorageInputLineInput] = Field(..., description="Items being received — at least one required", min_length=1)
    partner: Optional[str] = Field(default=None, description="Supplier partner code")
    note: Optional[str] = Field(default=None, description="Internal note")
    extra_fields: Optional[Dict[str, Any]] = Field(default=None, description="Additional API fields")


@mcp.tool(
    name="trade_create_storage_input_order",
    annotations={
        "title": "Create Storage Input Order",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trade_create_storage_input_order(params: CreateStorageInputOrderInput) -> str:
    """
    Create a storage input order — records goods being received into a warehouse/storage.

    Use this when stock arrives from a supplier and needs to be recorded in the system.

    Args:
        params (CreateStorageInputOrderInput):
            - document_date: date of receipt in YYYY-MM-DD (required)
            - storage: destination storage/warehouse code (required)
            - lines: items received with quantity and cost price (required)
            - partner: supplier partner code
            - note: optional internal note
            - extra_fields: additional API fields

    Returns:
        str: JSON of the created storage input order with its ISN
    """
    try:
        lines = [
            {
                "itemCode": ln.item_code,
                "quantity": ln.quantity,
                "price": ln.price,
                **({"unitMeasure": ln.unit_measure} if ln.unit_measure else {}),
            }
            for ln in params.lines
        ]
        body: dict = {
            "documentDate": params.document_date,
            "storage": params.storage,
            "lines": lines,
        }
        if params.partner:
            body["partner"] = params.partner
        if params.note:
            body["note"] = params.note
        if params.extra_fields:
            body.update(params.extra_fields)

        data = await _post("/documents/storageinputorder", body)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="trade_get_storage_input_order",
    annotations={
        "title": "Get Storage Input Order",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_storage_input_order(params: GetDocumentInput) -> str:
    """
    Retrieve a storage input order by its ISN.

    Args:
        params (GetDocumentInput):
            - isn: the storage input order ISN

    Returns:
        str: JSON storage input order document
    """
    try:
        data = await _get(f"/documents/storageinputorder/{params.isn}")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ── Storage Input Order Retail ───────────────────────────────────────────────

class CreateStorageInputOrderRetailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_date: str = Field(..., description="Date in YYYY-MM-DD format")
    storage: str = Field(..., description="Destination storage code", min_length=1)
    lines: List[StorageInputLineInput] = Field(..., description="Items being received — at least one required", min_length=1)
    note: Optional[str] = Field(default=None, description="Internal note")
    extra_fields: Optional[Dict[str, Any]] = Field(default=None, description="Additional API fields")


@mcp.tool(
    name="trade_create_storage_input_order_retail",
    annotations={
        "title": "Create Storage Input Order (Retail)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trade_create_storage_input_order_retail(params: CreateStorageInputOrderRetailInput) -> str:
    """
    Create a retail storage input order — for receiving goods at retail price into storage.

    Similar to a standard storage input order but uses retail pricing.

    Args:
        params (CreateStorageInputOrderRetailInput):
            - document_date: date of receipt in YYYY-MM-DD (required)
            - storage: destination storage code (required)
            - lines: items received with quantity and retail price (required)
            - note: optional internal note
            - extra_fields: additional API fields

    Returns:
        str: JSON of the created retail storage input order with its ISN
    """
    try:
        lines = [
            {
                "itemCode": ln.item_code,
                "quantity": ln.quantity,
                "price": ln.price,
                **({"unitMeasure": ln.unit_measure} if ln.unit_measure else {}),
            }
            for ln in params.lines
        ]
        body: dict = {
            "documentDate": params.document_date,
            "storage": params.storage,
            "lines": lines,
        }
        if params.note:
            body["note"] = params.note
        if params.extra_fields:
            body.update(params.extra_fields)

        data = await _post("/documents/storageinputorderretail", body)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="trade_get_storage_input_order_retail",
    annotations={
        "title": "Get Storage Input Order (Retail)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_storage_input_order_retail(params: GetDocumentInput) -> str:
    """
    Retrieve a retail storage input order by its ISN.

    Args:
        params (GetDocumentInput):
            - isn: the retail storage input order ISN

    Returns:
        str: JSON retail storage input order document
    """
    try:
        data = await _get(f"/documents/storageinputorderretail/{params.isn}")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ── Transfer Invoice ─────────────────────────────────────────────────────────

class TransferLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_code: str = Field(..., description="Product code", min_length=1)
    quantity: float = Field(..., description="Quantity to transfer", gt=0)
    unit_measure: Optional[str] = Field(default=None, description="Unit of measure code")


class CreateTransferInvoiceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_date: str = Field(..., description="Date in YYYY-MM-DD format")
    from_storage: str = Field(..., description="Source storage code to transfer goods FROM", min_length=1)
    to_storage: str = Field(..., description="Destination storage code to transfer goods TO", min_length=1)
    lines: List[TransferLineInput] = Field(..., description="Items to transfer — at least one required", min_length=1)
    note: Optional[str] = Field(default=None, description="Internal note")
    extra_fields: Optional[Dict[str, Any]] = Field(default=None, description="Additional API fields")


@mcp.tool(
    name="trade_create_transfer_invoice",
    annotations={
        "title": "Create Transfer Invoice",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trade_create_transfer_invoice(params: CreateTransferInvoiceInput) -> str:
    """
    Create a transfer invoice — moves goods from one storage/warehouse to another.

    Use when stock needs to be redistributed between locations.

    Args:
        params (CreateTransferInvoiceInput):
            - document_date: date of transfer in YYYY-MM-DD (required)
            - from_storage: source storage code (required)
            - to_storage: destination storage code (required)
            - lines: items to transfer with quantities (required)
            - note: optional internal note
            - extra_fields: additional API fields

    Returns:
        str: JSON of the created transfer invoice with its ISN
    """
    try:
        lines = [
            {
                "itemCode": ln.item_code,
                "quantity": ln.quantity,
                **({"unitMeasure": ln.unit_measure} if ln.unit_measure else {}),
            }
            for ln in params.lines
        ]
        body: dict = {
            "documentDate": params.document_date,
            "fromStorage": params.from_storage,
            "toStorage": params.to_storage,
            "lines": lines,
        }
        if params.note:
            body["note"] = params.note
        if params.extra_fields:
            body.update(params.extra_fields)

        data = await _post("/documents/transferinvoice", body)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="trade_get_transfer_invoice",
    annotations={
        "title": "Get Transfer Invoice",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_transfer_invoice(params: GetDocumentInput) -> str:
    """
    Retrieve a transfer invoice by its ISN.

    Args:
        params (GetDocumentInput):
            - isn: the transfer invoice ISN

    Returns:
        str: JSON transfer invoice with from/to storages and line items
    """
    try:
        data = await _get(f"/documents/transferinvoice/{params.isn}")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ── Products Movement ────────────────────────────────────────────────────────

class CreateProductsMovementInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_date: str = Field(..., description="Date in YYYY-MM-DD format")
    from_storage: str = Field(..., description="Source storage code", min_length=1)
    to_storage: str = Field(..., description="Destination storage code", min_length=1)
    lines: List[TransferLineInput] = Field(..., description="Items to move — at least one required", min_length=1)
    note: Optional[str] = Field(default=None, description="Internal note")
    extra_fields: Optional[Dict[str, Any]] = Field(default=None, description="Additional API fields")


@mcp.tool(
    name="trade_create_products_movement",
    annotations={
        "title": "Create Products Movement",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trade_create_products_movement(params: CreateProductsMovementInput) -> str:
    """
    Create a products movement document — records the physical movement of goods between storages.

    Similar to a transfer invoice but used specifically for internal stock movement tracking.

    Args:
        params (CreateProductsMovementInput):
            - document_date: date of movement in YYYY-MM-DD (required)
            - from_storage: source storage code (required)
            - to_storage: destination storage code (required)
            - lines: items being moved with quantities (required)
            - note: optional internal note
            - extra_fields: additional API fields

    Returns:
        str: JSON of the created products movement document with its ISN
    """
    try:
        lines = [
            {
                "itemCode": ln.item_code,
                "quantity": ln.quantity,
                **({"unitMeasure": ln.unit_measure} if ln.unit_measure else {}),
            }
            for ln in params.lines
        ]
        body: dict = {
            "documentDate": params.document_date,
            "fromStorage": params.from_storage,
            "toStorage": params.to_storage,
            "lines": lines,
        }
        if params.note:
            body["note"] = params.note
        if params.extra_fields:
            body.update(params.extra_fields)

        data = await _post("/documents/productsmovement", body)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="trade_get_products_movement",
    annotations={
        "title": "Get Products Movement",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_products_movement(params: GetDocumentInput) -> str:
    """
    Retrieve a products movement document by its ISN.

    Args:
        params (GetDocumentInput):
            - isn: the products movement document ISN

    Returns:
        str: JSON products movement document
    """
    try:
        data = await _get(f"/documents/productsmovement/{params.isn}")
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# TOOLS — REPORTS (additional)
# ===========================================================================

class ProductsBalancesShortInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    date: str = Field(..., description="Balance date in YYYY-MM-DD format")
    show_zero_rows: bool = Field(
        default=False,
        description="When true, includes products with zero stock. Default false (hides zero-stock items)."
    )
    storages: Optional[List[str]] = Field(
        default=None, description="Filter by storage codes. Omit for all storages."
    )


@mcp.tool(
    name="trade_get_products_balances_short",
    annotations={
        "title": "Get Products Balances (Short / Summary)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_products_balances_short(params: ProductsBalancesShortInput) -> str:
    """
    Get a compact summary of product stock balances for a given date.

    Returns less detail than trade_get_products_balances — ideal for quick stock checks,
    dashboards, or when you only need quantities without full product metadata.

    Args:
        params (ProductsBalancesShortInput):
            - date: balance date in YYYY-MM-DD (required)
            - show_zero_rows: include zero-stock items (default false)
            - storages: filter to specific storage codes

    Returns:
        str: JSON with { "data": [...balance rows], "count": int }
    """
    try:
        body: dict = {"date": params.date, "showZeroRows": params.show_zero_rows}
        if params.storages:
            body["storages"] = params.storages

        all_items = await _fetch_all_pages(
            "/reports/productsbalances/short",
            "/reports/productsbalances/short/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


class BonusBalancesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    date: str = Field(..., description="Balance date in YYYY-MM-DD format")
    partner_code: Optional[str] = Field(
        default=None, description="Filter to a specific partner code"
    )


@mcp.tool(
    name="trade_get_bonus_balances",
    annotations={
        "title": "Get Bonus Balances",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_bonus_balances(params: BonusBalancesInput) -> str:
    """
    Get partner loyalty/bonus point balances for a given date.

    Use for loyalty program reporting, partner statements, or triggering
    bonus-based promotions in n8n workflows.

    Args:
        params (BonusBalancesInput):
            - date: balance date in YYYY-MM-DD (required)
            - partner_code: filter to one specific partner

    Returns:
        str: JSON with { "data": [...bonus balance rows], "count": int }
        Each row shows partner name and their accumulated bonus balance.
    """
    try:
        body: dict = {"date": params.date}
        if params.partner_code:
            body["partnerCode"] = params.partner_code

        all_items = await _fetch_all_pages(
            "/reports/bonusbalances",
            "/reports/bonusbalances/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# TOOLS — JOURNALS (additional)
# ===========================================================================

class EcrChecksInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    start_date: str = Field(..., description="Journal start date in YYYY-MM-DD format")
    end_date: str = Field(..., description="Journal end date in YYYY-MM-DD format")
    cashdesk_code: Optional[str] = Field(
        default=None, description="Filter by cash desk code"
    )
    show_payments: bool = Field(
        default=True, description="Include payment details in results (default true)"
    )


@mcp.tool(
    name="trade_get_ecr_checks",
    annotations={
        "title": "Get ECR Checks Journal",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def trade_get_ecr_checks(params: EcrChecksInput) -> str:
    """
    Get the ECR (Electronic Cash Register) checks journal for a date range.

    Returns a log of all cash register receipts/checks. Use for end-of-day
    reconciliation, cashdesk reporting, or syncing POS data to external systems.

    Args:
        params (EcrChecksInput):
            - start_date: period start in YYYY-MM-DD (required)
            - end_date: period end in YYYY-MM-DD (required)
            - cashdesk_code: filter to a specific cash desk
            - show_payments: include payment breakdowns (default true)

    Returns:
        str: JSON with { "data": [...ECR check rows], "count": int }
        Each row contains receipt number, date, amount, cashdesk, and payment info.

    Examples:
        - Daily cashdesk reconciliation: start_date=today, end_date=today, cashdesk_code="CD001"
        - Monthly POS export: start_date="2026-03-01", end_date="2026-03-31"
    """
    try:
        body: dict = {
            "startDate": params.start_date,
            "endDate": params.end_date,
            "showPayments": params.show_payments,
        }
        if params.cashdesk_code:
            body["cashDeskCode"] = params.cashdesk_code

        all_items = await _fetch_all_pages(
            "/journals/ecrchecks",
            "/journals/ecrchecks/nextpage",
            body,
        )
        return json.dumps(_truncate(all_items), ensure_ascii=False, indent=2)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    import sys

    if not API_KEY:
        print(
            "WARNING: ARMSOFT_API_KEY environment variable is not set. "
            "All API calls will return 401 Unauthorized.",
            file=sys.stderr,
        )

    import contextlib
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    port = int(os.getenv("PORT", 8000))

    # Build a bare Starlette app using the MCP StreamableHTTPSessionManager
    # directly — without going through FastMCP's run() method and without
    # the TransportSecurityMiddleware that rejects external Host headers.
    _custom_app = None
    try:
        # Try the known import paths for the session manager across SDK versions
        _SessionManager = None
        for _mod_path in (
            "mcp.server.streamable_http_manager",
            "mcp.server.http",
            "mcp.server.streamable_http",
        ):
            try:
                import importlib as _il
                _m = _il.import_module(_mod_path)
                if hasattr(_m, "StreamableHTTPSessionManager"):
                    _SessionManager = _m.StreamableHTTPSessionManager
                    break
            except ImportError:
                continue

        # Get the low-level MCP Server object wrapped by FastMCP
        _underlying = None
        for _attr in ("_mcp_server", "server", "_server", "_app"):
            if hasattr(mcp, _attr):
                _underlying = getattr(mcp, _attr)
                break

        if _SessionManager is not None and _underlying is not None:
            _mgr = _SessionManager(
                app=_underlying,
                event_store=None,
                json_response=False,
                stateless=False,
            )

            @contextlib.asynccontextmanager
            async def _lifespan(app):
                async with _mgr.run():
                    yield

            async def _handle(scope, receive, send):
                await _mgr.handle_request(scope, receive, send)

            _custom_app = Starlette(
                routes=[Mount("/mcp", app=_handle)],
                lifespan=_lifespan,
            )
    except Exception as _err:
        print(f"Custom server setup failed: {_err}", file=sys.stderr)

    if _custom_app is not None:
        uvicorn.run(_custom_app, host="0.0.0.0", port=port)
    else:
        # Fallback to the standard run path
        mcp.settings.port = port
        mcp.settings.host = "0.0.0.0"
        mcp.run(transport="streamable-http")