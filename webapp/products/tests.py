import csv
import io
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from django.core import signing
from django.core.cache import cache
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import collection_csv, collection_fast_client, firecrawl_client, views


class CollectionDiscoveryTests(SimpleTestCase):
    def test_reads_advertised_search_result_counts(self):
        self.assertEqual(
            firecrawl_client._advertised_product_count(
                '412 results found for "hoodie"\n412 Products'
            ),
            412,
        )
        self.assertEqual(
            firecrawl_client._advertised_product_count("2,365 items"),
            2365,
        )
        self.assertEqual(
            firecrawl_client._advertised_product_count("Mickey Mouse Hoodies 485 results"),
            485,
        )
        self.assertEqual(
            firecrawl_client._advertised_product_count("Showing 1 - 60 of 1,049"),
            1049,
        )
        self.assertIsNone(
            firecrawl_client._advertised_product_count("No total shown")
        )

    def test_search_url_does_not_receive_collection_sort(self):
        url = "https://shop.example/search?page=3&limit=30&q=hoodie#grid"

        self.assertEqual(
            firecrawl_client._stable_collection_url(url),
            "https://shop.example/search?page=3&limit=30&q=hoodie",
        )
        self.assertEqual(
            firecrawl_client._search_start_url(url),
            "https://shop.example/search?limit=30&q=hoodie&page=1",
        )
        self.assertTrue(
            firecrawl_client._is_search_results_url(
                "https://teehobbies.us/?s=mickey+hoodie"
            )
        )

    def test_pumpbear_search_removes_obsolete_shopbase_parameters(self):
        url = (
            "https://pumpbear.com/search?page=7&limit=30&minimal=true"
            "&q=touchdown&infinite=false&sort=most_related"
            "&sort_field=most_related&sort_direction=desc"
        )

        self.assertTrue(firecrawl_client._is_pumpbear_search_url(url))
        self.assertEqual(
            firecrawl_client._pumpbear_search_start_url(url),
            "https://pumpbear.com/search?q=touchdown&sort=relevance&page=1",
        )

    @patch.object(firecrawl_client, "_post_scrape_with_retry")
    def test_pumpbear_search_browser_collects_and_validates_every_page(self, post_scrape):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {"javascriptReturns": [{"value": {
                    "advertised_count": 3,
                    "pages_scanned": 2,
                    "product_urls": [
                        "https://pumpbear.com/products/a?variant=1",
                        "https://pumpbear.com/products/b",
                        "https://pumpbear.com/products/c",
                    ],
                    "products": [{
                        "title": "Product A",
                        "price": 99,
                        "currency": "USD",
                        "product_url": "https://pumpbear.com/products/a",
                        "images": [
                            "https://cdn.pumpbear.com/products/1/front.jpg",
                            "https://cdn.pumpbear.com/products/1/back.jpg",
                        ],
                    }],
                    "failed_pages": [],
                }}]},
            },
        }
        post_scrape.return_value = response

        result = firecrawl_client._discover_pumpbear_search_product_links(
            MagicMock(),
            "https://pumpbear.com/search?page=9&limit=30&q=touchdown&sort=most_related",
            domain="pumpbear.com",
            path_hint="/products/",
            max_products=100,
        )

        self.assertEqual(len(result["urls"]), 3)
        self.assertEqual(result["advertised_count"], 3)
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(result["credits_used"], 1)
        self.assertEqual(result["discovery_mode"], "pumpbear_search_html")
        self.assertEqual(len(result["products"]), 1)
        payload = post_scrape.call_args.args[1]
        self.assertEqual(
            payload["url"],
            "https://pumpbear.com/search?q=touchdown&sort=relevance&page=1",
        )
        self.assertIn("Promise.all", payload["actions"][1]["script"])

    def test_collection_page_url_restarts_at_one_and_preserves_filters(self):
        url = "https://shop.example/collections/chairs?sort_by=best&page_num=7&cursor=old#grid"

        self.assertEqual(
            firecrawl_client._collection_page_url(url, 1),
            "https://shop.example/collections/chairs?sort_by=best",
        )
        self.assertEqual(
            firecrawl_client._collection_page_url(url, 3),
            "https://shop.example/collections/chairs?sort_by=best&page=3",
        )

    def test_collection_url_uses_stable_sort_unless_caller_supplies_one(self):
        self.assertEqual(
            firecrawl_client._stable_collection_url(
                "https://shop.example/collections/chairs?page_num=4#grid"
            ),
            "https://shop.example/collections/chairs?page_num=4&sort_by=shelved-descending",
        )
        self.assertEqual(
            firecrawl_client._stable_collection_url(
                "https://shop.example/collections/chairs?sort_by=recommend"
            ),
            "https://shop.example/collections/chairs?sort_by=shelved-descending",
        )
        self.assertEqual(
            firecrawl_client._stable_collection_url(
                "https://shop.example/collections/chairs?sort_by=price-ascending#grid"
            ),
            "https://shop.example/collections/chairs?sort_by=price-ascending",
        )
        self.assertEqual(
            firecrawl_client._stable_collection_url(
                "https://www.amazon.com/s?k=hoodie#results"
            ),
            "https://www.amazon.com/s?k=hoodie",
        )

    def test_canonicalises_common_marketplace_product_urls(self):
        cases = [
            (
                "https://www.amazon.com/Some-Title/dp/B0ABC12345/ref=sr_1_1?tag=x",
                "https://www.amazon.com/dp/B0ABC12345",
            ),
            (
                "https://www.ebay.com/itm/Product-Name/123456789012?hash=x",
                "https://www.ebay.com/itm/123456789012",
            ),
            (
                "https://www.walmart.com/ip/Product-Name/987654321?athbdg=x",
                "https://www.walmart.com/ip/987654321",
            ),
            (
                "https://www.aliexpress.com/item/1005001234567890.html?spm=x",
                "https://www.aliexpress.com/item/1005001234567890.html",
            ),
            (
                "https://store.example/product/hoodie?color=red",
                "https://store.example/product/hoodie",
            ),
            (
                "https://printerval.com/uk/birra-moretti-beer-ugly-christmas-sweater-p3617475?variant=1",
                "https://printerval.com/uk/birra-moretti-beer-ugly-christmas-sweater-p3617475",
            ),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(
                    firecrawl_client._canonical_product_link(
                        source,
                        source,
                        urlparse(source).hostname,
                        "/products/",
                    ),
                    expected,
                )

    def test_canonicalises_etsy_listing_and_drops_tracking_query(self):
        url, listing_id = firecrawl_client._canonical_etsy_listing_link(
            "https://www.etsy.com/listing/4353523535/mickey-sunshade"
            "?ref=shop_items_search_1&logging_key=secret",
            "https://www.etsy.com/shop/MichaelOrn",
        )

        self.assertEqual(listing_id, "4353523535")
        self.assertEqual(
            url,
            "https://www.etsy.com/listing/4353523535/mickey-sunshade",
        )

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client, "_scrape_links")
    @patch.object(firecrawl_client.httpx, "Client")
    def test_etsy_section_follows_pages_and_excludes_review_links(
        self, client_cls, scrape_links, _check_key
    ):
        first_page = [
            f"https://www.etsy.com/listing/{1000 + index}/item-{index}"
            f"?ref=shop_home_active_{index + 1}"
            for index in range(36)
        ]
        first_page.append(
            "https://www.etsy.com/listing/9999/review-item?ref=shop_review"
        )
        second_page = [
            "https://www.etsy.com/listing/2001/item-a?ref=shop_home_active_1",
            "https://www.etsy.com/listing/2002/item-b?ref=shop_home_active_2",
        ]
        scrape_links.side_effect = [first_page, second_page]

        result = firecrawl_client.discover_etsy_listing_links(
            "https://www.etsy.com/shop/MichaelOrn"
            "?ref=shop_profile&listing_id=4548410826&section_id=57567238"
        )

        self.assertEqual(len(result["urls"]), 38)
        self.assertNotIn("9999", " ".join(result["urls"]))
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(result["discovery_mode"], "etsy_section")
        requested_pages = [call.args[1] for call in scrape_links.call_args_list]
        self.assertEqual(
            requested_pages,
            [
                "https://www.etsy.com/shop/MichaelOrn?section_id=57567238",
                "https://www.etsy.com/shop/MichaelOrn?section_id=57567238&page=2",
            ],
        )

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client, "_scrape_links")
    @patch.object(firecrawl_client.httpx, "Client")
    def test_etsy_search_keeps_only_search_grid_links(
        self, client_cls, scrape_links, _check_key
    ):
        scrape_links.return_value = [
            "https://www.etsy.com/listing/1/mickey?ref=shop_items_search_1",
            "https://www.etsy.com/listing/2/review?ref=shop_review",
        ]

        result = firecrawl_client.discover_etsy_listing_links(
            "https://www.etsy.com/shop/MichaelOrn?search_query=mickey"
        )

        self.assertEqual(
            result["urls"],
            ["https://www.etsy.com/listing/1/mickey"],
        )
        self.assertEqual(result["discovery_mode"], "etsy_search")

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client, "_scrape_links")
    @patch.object(firecrawl_client.httpx, "Client")
    def test_discovers_all_pages_and_deduplicates_variant_links(self, client_cls, scrape_links, _check_key):
        scrape_links.side_effect = [
            [
                "https://shop.example/products/a?variant=1",
                "https://shop.example/products/b",
                "https://other.example/products/not-ours",
                "https://shop.example/collections/chairs?page_num=2",
            ],
            [
                "https://shop.example/products/a?variant=2",
                "/products/c#gallery",
                "/collections/chairs?page_num=1",
                "/collections/chairs?page_num=3",
            ],
            [],
        ]

        result = firecrawl_client.discover_collection_product_links(
            "https://shop.example/collections/chairs?page=9"
        )

        self.assertEqual(
            result["urls"],
            [
                "https://shop.example/products/a",
                "https://shop.example/products/b",
                "https://shop.example/products/c",
            ],
        )
        self.assertEqual(result["pages_scanned"], 3)
        self.assertEqual(result["credits_used"], 3)
        self.assertFalse(result["truncated"])
        requested_pages = [call.args[1] for call in scrape_links.call_args_list]
        self.assertEqual(
            requested_pages,
            [
                "https://shop.example/collections/chairs?sort_by=shelved-descending",
                "https://shop.example/collections/chairs?page_num=2&sort_by=shelved-descending",
                "https://shop.example/collections/chairs?page_num=3&sort_by=shelved-descending",
            ],
        )

    def test_recognises_common_collection_pagination_styles(self):
        collection = "https://shop.example/collections/chairs"
        self.assertEqual(
            firecrawl_client._collection_pagination_link(
                "/collections/chairs?page_num=2", collection, collection
            ),
            "https://shop.example/collections/chairs?page_num=2",
        )
        self.assertEqual(
            firecrawl_client._collection_pagination_link(
                "/collections/chairs?cursor=abc", collection, collection
            ),
            "https://shop.example/collections/chairs?cursor=abc",
        )
        self.assertEqual(
            firecrawl_client._collection_pagination_link(
                "/collections/chairs?page=1", collection, collection
            ),
            "",
        )
        self.assertEqual(
            firecrawl_client._collection_pagination_link(
                "/collections/tables?page=2", collection, collection
            ),
            "",
        )

    def test_pagination_preserves_collection_sort_and_filters(self):
        collection = (
            "https://shop.example/collections/chairs"
            "?tag=wood&sort_by=shelved-descending"
        )
        self.assertEqual(
            firecrawl_client._collection_pagination_link(
                "/collections/chairs?page_num=2", collection, collection
            ),
            (
                "https://shop.example/collections/chairs"
                "?page_num=2&tag=wood&sort_by=shelved-descending"
            ),
        )

    def test_infinite_scroll_accumulates_unique_urls_and_checks_total(self):
        initial_response = MagicMock()
        initial_response.is_error = False
        initial_response.json.return_value = {
            "data": {
                "markdown": '4 results found for "hoodie"',
                "links": [
                    "https://shop.example/products/a",
                    "https://shop.example/products/b",
                ],
            }
        }
        scrolled_response = MagicMock()
        scrolled_response.is_error = False
        scrolled_response.json.return_value = {
            "data": {
                "links": ["https://shop.example/products/d"],
                "actions": {
                    "javascriptReturns": [
                        {"type": "number", "value": 2},
                        {
                            "type": "object",
                            "value": [
                                "https://shop.example/products/a?variant=1",
                                "https://shop.example/products/b",
                                "https://shop.example/products/c",
                            ],
                        },
                    ]
                },
            }
        }
        client = MagicMock()
        client.post.side_effect = [initial_response, scrolled_response]

        result = firecrawl_client._discover_infinite_scroll_product_links(
            client,
            "https://shop.example/search?limit=2&q=hoodie",
            domain="shop.example",
            path_hint="/products/",
            max_products=10,
        )

        self.assertEqual(
            result["urls"],
            [
                "https://shop.example/products/a",
                "https://shop.example/products/b",
                "https://shop.example/products/c",
                "https://shop.example/products/d",
            ],
        )
        self.assertEqual(result["advertised_count"], 4)
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(result["credits_used"], 2)
        self.assertEqual(result["discovery_mode"], "infinite_scroll")
        action_payload = client.post.call_args_list[1].kwargs["json"]
        self.assertTrue(
            any(
                action.get("type") == "executeJavascript"
                for action in action_payload["actions"]
            )
        )
        action_scripts = " ".join(
            action.get("script", "") for action in action_payload["actions"]
        )
        self.assertIn("printerval", action_scripts)
        self.assertIn("-p\\d+", action_scripts)

    def test_printerval_root_slug_is_collected_as_a_product(self):
        initial_response = MagicMock()
        initial_response.is_error = False
        initial_response.json.return_value = {
            "data": {
                "markdown": "About 2 Results",
                "links": [
                    "https://printerval.com/coors-beer-sweater-p100?ref=search",
                ],
            }
        }
        scrolled_response = MagicMock()
        scrolled_response.is_error = False
        scrolled_response.json.return_value = {
            "data": {
                "actions": {
                    "javascriptReturns": [{
                        "value": [
                            "https://printerval.com/coors-beer-sweater-p100",
                            "https://printerval.com/uk/guinness-beer-sweater-p200",
                        ]
                    }]
                }
            }
        }
        client = MagicMock()
        client.post.side_effect = [initial_response, scrolled_response]

        result = firecrawl_client._discover_infinite_scroll_product_links(
            client,
            "https://printerval.com/search?q=beer+sweater",
            domain="printerval.com",
            path_hint="/products/",
            max_products=1000,
        )

        self.assertEqual(len(result["urls"]), 2)
        self.assertEqual(result["advertised_count"], 2)
        self.assertEqual(result["discovery_mode"], "infinite_scroll")

    def test_shopify_collection_browser_api_returns_complete_galleries(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {
                    "javascriptReturns": [{
                        "value": {
                            "supported": True,
                            "source": "shopify_api",
                            "advertised_count": 2,
                            "pages_scanned": 1,
                            "products": [
                                {
                                    "title": "Beer Sweater A",
                                    "price": 49.95,
                                    "compare_at_price": None,
                                    "currency": "USD",
                                    "product_url": "https://flexiquor.com/products/beer-a",
                                    "images": [
                                        "https://cdn.example/a.jpg",
                                        "https://cdn.example/a-back.jpg",
                                    ],
                                },
                                {
                                    "title": "Beer Sweater B",
                                    "price": 49.95,
                                    "compare_at_price": 59.95,
                                    "currency": "USD",
                                    "product_url": "https://flexiquor.com/products/beer-b",
                                    "images": ["https://cdn.example/b.jpg"],
                                },
                            ],
                        }
                    }]
                },
            }
        }
        client = MagicMock()
        client.post.return_value = response

        result = firecrawl_client._discover_shopify_collection_via_browser(
            client,
            "https://flexiquor.com/collections/ugly-sweater/",
            domain="flexiquor.com",
            path_hint="/products/",
            max_products=10_000,
        )

        self.assertEqual(len(result["urls"]), 2)
        self.assertEqual(len(result["products"]), 2)
        self.assertEqual(
            result["products"][0]["images"],
            ["https://cdn.example/a.jpg", "https://cdn.example/a-back.jpg"],
        )
        self.assertTrue(result["full_gallery_available"])
        self.assertEqual(result["discovery_mode"], "shopify_collection_api")
        script = client.post.call_args.kwargs["json"]["actions"][1]["script"]
        self.assertIn("/products.json", script)
        self.assertIn("const pageSize = 250", script)
        self.assertIn("const productsByKey = new Map()", script)
        self.assertIn("if (!added) break", script)
        self.assertNotIn("if (batch.length < pageSize) break", script)

    def test_shopify_search_html_fetches_every_real_page(self):
        def response(links, advertised_count=5):
            value = MagicMock()
            value.is_error = False
            anchors = "".join(
                f'<a href="/products/{handle}?_pos={index}&_ss=r">{handle}</a>'
                for index, handle in enumerate(links, 1)
            )
            value.text = (
                '<html><head><script src="https://cdn.shopify.com/theme.js"></script></head>'
                f'<body><h2>{advertised_count} results</h2>{anchors}</body></html>'
            )
            return value

        pages = {
            1: response(["a", "b"]),
            2: response(["c", "d"]),
            3: response(["e"]),
            4: response([], advertised_count=0),
        }
        client = MagicMock()
        client.get.side_effect = lambda url, **kwargs: pages[
            int(parse_qs(urlparse(url).query)["page"][0])
        ]

        result = firecrawl_client._discover_shopify_search_product_links(
            client,
            "https://shop.example/search?q=sweater&page=2",
            domain="shop.example",
            path_hint="/products/",
            max_products=100,
            strict=True,
        )

        self.assertEqual(result["urls"], [
            "https://shop.example/products/a",
            "https://shop.example/products/b",
            "https://shop.example/products/c",
            "https://shop.example/products/d",
            "https://shop.example/products/e",
        ])
        self.assertEqual(result["pages_scanned"], 3)
        self.assertEqual(result["credits_used"], 0)
        self.assertEqual(result["discovery_mode"], "shopify_search_html")
        self.assertTrue(all(
            parse_qs(urlparse(call.args[0]).query).get("type") == ["product"]
            for call in client.get.call_args_list
        ))

    def test_shopify_search_uses_storefront_graphql_when_theme_count_is_wrong(self):
        html_response = MagicMock()
        html_response.status_code = 200
        html_response.is_error = False
        html_response.headers = {}
        html_response.text = (
            '<html><head><script src="https://cdn.shopify.com/theme.js"></script>'
            "<script>_TC.storeFrontAccessToken = "
            "'0123456789abcdef0123456789abcdef';</script></head>"
            '<body><h2>304 results</h2>'
            '<a href="/products/rendered-only?_pos=1&_ss=r">Rendered</a>'
            '</body></html>'
        )
        graphql_response = MagicMock()
        graphql_response.status_code = 200
        graphql_response.is_error = False
        graphql_response.headers = {}
        graphql_response.json.return_value = {
            "data": {
                "products": {
                    "nodes": [
                        {
                            "id": "gid://shopify/Product/1",
                            "handle": "hoodie-a",
                            "title": "Hoodie A",
                            "onlineStoreUrl": (
                                "https://shop.example/products/hoodie-a?variant=1"
                            ),
                            "priceRange": {
                                "minVariantPrice": {
                                    "amount": "49.95",
                                    "currencyCode": "USD",
                                }
                            },
                            "compareAtPriceRange": {
                                "minVariantPrice": {
                                    "amount": "69.95",
                                    "currencyCode": "USD",
                                }
                            },
                            "images": {"nodes": [
                                {"url": "https://cdn.example/a-front.jpg"},
                                {"url": "https://cdn.example/a-back.jpg"},
                            ]},
                        },
                        {
                            "id": "gid://shopify/Product/2",
                            "handle": "hoodie-b",
                            "title": "Hoodie B",
                            "onlineStoreUrl": None,
                            "priceRange": {
                                "minVariantPrice": {
                                    "amount": "39.00",
                                    "currencyCode": "USD",
                                }
                            },
                            "compareAtPriceRange": {
                                "minVariantPrice": {
                                    "amount": "0.0",
                                    "currencyCode": "USD",
                                }
                            },
                            "images": {"nodes": [
                                {"url": "https://cdn.example/b.jpg"},
                            ]},
                        },
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": "end"},
                }
            }
        }
        client = MagicMock()
        client.get.return_value = html_response
        client.post.return_value = graphql_response

        result = firecrawl_client._discover_shopify_search_product_links(
            client,
            "https://shop.example/search?q=hoodie&page=3",
            domain="shop.example",
            path_hint="/products/",
            max_products=100,
            strict=True,
        )

        self.assertEqual(result["urls"], [
            "https://shop.example/products/hoodie-a",
            "https://shop.example/products/hoodie-b",
        ])
        self.assertEqual(result["advertised_count"], 2)
        self.assertEqual(result["html_advertised_count"], 304)
        self.assertEqual(result["discovery_mode"], "shopify_storefront_graphql")
        self.assertEqual(result["fallback_product_count"], 0)
        self.assertTrue(result["full_gallery_available"])
        self.assertEqual(result["products"][0]["compare_at_price"], 69.95)
        self.assertIsNone(result["products"][1]["compare_at_price"])
        self.assertEqual(result["products"][0]["images"], [
            "https://cdn.example/a-front.jpg",
            "https://cdn.example/a-back.jpg",
        ])
        client.get.assert_called_once()
        client.post.assert_called_once()
        graphql_call = client.post.call_args
        self.assertIn("/api/2026-10/graphql.json", graphql_call.args[0])
        self.assertEqual(graphql_call.kwargs["json"]["variables"]["query"], "hoodie")

    @patch.object(firecrawl_client.time, "sleep")
    def test_shopify_search_retries_rate_limited_page(self, sleep):
        def response(links, advertised_count=2, status_code=200):
            value = MagicMock()
            value.status_code = status_code
            value.is_error = status_code >= 400
            value.headers = {"Retry-After": "0.01"} if status_code == 429 else {}
            anchors = "".join(
                f'<a href="/products/{handle}?_pos={index}&_ss=r">{handle}</a>'
                for index, handle in enumerate(links, 1)
            )
            value.text = (
                '<html><head><script src="https://cdn.shopify.com/theme.js"></script></head>'
                f'<body><h2>{advertised_count} results</h2>{anchors}</body></html>'
            )
            return value

        calls = {}

        def load(url, **kwargs):
            page = int(parse_qs(urlparse(url).query)["page"][0])
            calls[page] = calls.get(page, 0) + 1
            if page == 1 and calls[page] == 1:
                return response([], status_code=429)
            return response(["a", "b"]) if page == 1 else response([], 0)

        client = MagicMock()
        client.get.side_effect = load

        result = firecrawl_client._discover_shopify_search_product_links(
            client,
            "https://shop.example/search?q=sweater",
            domain="shop.example",
            path_hint="/products/",
            max_products=100,
            strict=True,
        )

        self.assertEqual(result["urls"], [
            "https://shop.example/products/a",
            "https://shop.example/products/b",
        ])
        self.assertEqual(calls[1], 2)
        sleep.assert_called_once_with(0.5)

    def test_woocommerce_collection_api_returns_complete_galleries(self):
        category_response = MagicMock()
        category_response.is_error = False
        category_response.json.return_value = [
            {"id": 435, "slug": "ugly-sweater", "count": 2}
        ]
        first_response = MagicMock()
        first_response.is_error = False
        first_response.headers = {"x-wp-total": "2"}
        first_response.json.return_value = [
            {
                "id": 10,
                "name": "Beer Sweater A",
                "permalink": "https://flexiquor.com/product/beer-a/",
                "prices": {
                    "price": "4995",
                    "regular_price": "5995",
                    "currency_code": "USD",
                    "currency_minor_unit": 2,
                },
                "images": [
                    {"src": "https://cdn.example/a.jpg"},
                    {"src": "https://cdn.example/a-back.jpg"},
                    {"src": "https://cdn.example/a-detail.jpg"},
                ],
            },
            {
                "id": 11,
                "name": "Beer Sweater B",
                "permalink": "https://flexiquor.com/product/beer-b/",
                "prices": {
                    "price": "4995",
                    "regular_price": "4995",
                    "currency_code": "USD",
                    "currency_minor_unit": 2,
                },
                "images": [{"src": "https://cdn.example/b.jpg"}],
            },
        ]
        client = MagicMock()
        client.get.side_effect = [category_response, first_response]

        result = firecrawl_client._discover_woocommerce_collection_products(
            client,
            "https://flexiquor.com/collections/ugly-sweater/",
            max_products=10_000,
        )

        self.assertEqual(len(result["urls"]), 2)
        self.assertEqual(result["advertised_count"], 2)
        self.assertEqual(result["credits_used"], 0)
        self.assertEqual(result["discovery_mode"], "woocommerce_store_api")
        self.assertTrue(result["full_gallery_available"])
        self.assertEqual(result["products"][0]["price"], 49.95)
        self.assertEqual(result["products"][0]["compare_at_price"], 59.95)
        self.assertEqual(
            result["products"][0]["images"],
            [
                "https://cdn.example/a.jpg",
                "https://cdn.example/a-back.jpg",
                "https://cdn.example/a-detail.jpg",
            ],
        )

    def test_woocommerce_search_joins_rest_ids_to_complete_store_galleries(self):
        search_response = MagicMock()
        search_response.is_error = False
        search_response.headers = {"x-wp-total": "2"}
        search_response.json.return_value = [
            {
                "id": 10,
                "title": "Varsity Jacket A",
                "url": "https://royaljacket.com/product/varsity-a/",
                "subtype": "product",
            },
            {
                "id": 11,
                "title": "Varsity Jacket B",
                "url": "https://royaljacket.com/product/varsity-b/",
                "subtype": "product",
            },
        ]
        store_response = MagicMock()
        store_response.is_error = False
        store_response.json.return_value = [
            {
                "id": 11,
                "name": "Varsity Jacket B",
                "permalink": "https://royaljacket.com/product/varsity-b/",
                "prices": {
                    "price": "4995",
                    "regular_price": "4995",
                    "currency_code": "USD",
                    "currency_minor_unit": 2,
                },
                "images": [{"src": "https://cdn.example/b.jpg"}],
            },
            {
                "id": 10,
                "name": "Varsity Jacket A",
                "permalink": "https://royaljacket.com/product/varsity-a/",
                "prices": {
                    "price": "4995",
                    "regular_price": "5995",
                    "currency_code": "USD",
                    "currency_minor_unit": 2,
                },
                "images": [
                    {"src": "https://cdn.example/a-front.jpg"},
                    {"src": "https://cdn.example/a-back.jpg"},
                ],
            },
        ]
        client = MagicMock()
        client.get.side_effect = [search_response, store_response]

        result = firecrawl_client._discover_woocommerce_collection_products(
            client,
            (
                "https://royaljacket.com/page/15/"
                "?s=Varsity+jacket&post_type=product"
            ),
            max_products=1000,
        )

        self.assertEqual(result["advertised_count"], 2)
        self.assertEqual(result["credits_used"], 0)
        self.assertEqual(result["fallback_product_count"], 0)
        self.assertEqual(result["discovery_mode"], "woocommerce_store_api")
        self.assertTrue(result["full_gallery_available"])
        self.assertEqual(
            result["urls"],
            [
                "https://royaljacket.com/product/varsity-a/",
                "https://royaljacket.com/product/varsity-b/",
            ],
        )
        self.assertEqual(
            result["products"][0]["images"],
            [
                "https://cdn.example/a-front.jpg",
                "https://cdn.example/a-back.jpg",
            ],
        )
        self.assertTrue(
            client.get.call_args_list[0].args[0].endswith("/wp-json/wp/v2/search")
        )
        self.assertEqual(
            client.get.call_args_list[1].kwargs["params"]["include"],
            "10,11",
        )

    @patch.object(firecrawl_client.time, "sleep")
    def test_woocommerce_search_does_not_refetch_unpaginated_full_response(
        self,
        _sleep,
    ):
        search_items = [
            {
                "id": product_id,
                "title": f"Air Max {product_id}",
                "url": f"https://konfox.example/product/air-max-{product_id}/",
                "subtype": "product",
            }
            for product_id in range(1, 151)
        ]
        search_response = MagicMock()
        search_response.is_error = False
        search_response.headers = {"x-wp-total": "150"}
        search_response.json.return_value = search_items

        client = MagicMock()
        store_attempts = {}

        def get_response(url, *, params, **kwargs):
            if url.endswith("/wp-json/wp/v2/search"):
                self.assertEqual(params["page"], 1)
                return search_response
            self.assertTrue(url.endswith("/wp-json/wc/store/v1/products"))
            included_ids = [int(value) for value in params["include"].split(",")]
            chunk_key = tuple(included_ids)
            store_attempts[chunk_key] = store_attempts.get(chunk_key, 0) + 1
            if 109 in included_ids:
                transient_response = MagicMock()
                transient_response.is_error = True
                transient_response.status_code = 502
                return transient_response
            response = MagicMock()
            response.is_error = False
            response.status_code = 200
            response.json.return_value = [
                {
                    "id": product_id,
                    "name": f"Air Max {product_id}",
                    "permalink": (
                        f"https://konfox.example/product/air-max-{product_id}/"
                    ),
                    "prices": {
                        "price": "6995",
                        "regular_price": "6995",
                        "currency_code": "USD",
                        "currency_minor_unit": 2,
                    },
                    "images": [
                        {"src": f"https://cdn.example/air-max-{product_id}.jpg"}
                    ],
                }
                for product_id in included_ids
            ]
            return response

        client.get.side_effect = get_response

        result = firecrawl_client._discover_woocommerce_collection_products(
            client,
            "https://konfox.example/?s=Air+Max&post_type=product",
            max_products=50_000,
            strict=True,
        )

        search_calls = [
            call
            for call in client.get.call_args_list
            if call.args[0].endswith("/wp-json/wp/v2/search")
        ]
        self.assertEqual(len(search_calls), 1)
        self.assertEqual(result["advertised_count"], 150)
        self.assertEqual(result["pages_scanned"], 1)
        self.assertEqual(len(result["urls"]), 150)
        self.assertEqual(len(result["products"]), 149)
        self.assertEqual(result["fallback_product_count"], 1)
        self.assertFalse(result["full_gallery_available"])
        self.assertEqual(store_attempts[(109,)], 2)

    def test_woocommerce_collection_skips_rows_whose_store_api_images_are_empty(self):
        category_response = MagicMock()
        category_response.is_error = False
        category_response.json.return_value = [
            {"id": 109, "slug": "aj1", "count": 2}
        ]
        first_response = MagicMock()
        first_response.is_error = False
        first_response.headers = {"x-wp-total": "2"}
        first_response.json.return_value = [
            {
                "id": 10,
                "name": "AJ1 Shoe With Gallery",
                "permalink": "https://shop.example/product/aj1-a/",
                "prices": {
                    "price": "7895",
                    "regular_price": "7895",
                    "currency_code": "USD",
                    "currency_minor_unit": 2,
                },
                "images": [
                    {"src": "https://cdn.example/a-front.jpg"},
                    {"src": "https://cdn.example/a-back.jpg"},
                ],
            },
            {
                "id": 11,
                "name": "AJ1 Shoe Missing Store Images",
                "permalink": "https://shop.example/product/aj1-b/",
                "prices": {
                    "price": "7895",
                    "regular_price": "7895",
                    "currency_code": "USD",
                    "currency_minor_unit": 2,
                },
                "images": [],
            },
        ]
        client = MagicMock()
        client.get.side_effect = [category_response, first_response]

        result = firecrawl_client._discover_woocommerce_collection_products(
            client,
            "https://shop.example/shop/?filter_category=aj1",
            max_products=10_000,
            strict=True,
        )

        self.assertEqual(result["urls"], ["https://shop.example/product/aj1-a/"])
        self.assertEqual(len(result["products"]), 1)
        self.assertTrue(result["full_gallery_available"])
        self.assertEqual(result["fallback_product_count"], 0)
        self.assertEqual(result["skipped_no_image_count"], 1)
        self.assertEqual(result["card_diagnostics"]["with_images"], 1)
        self.assertEqual(result["card_diagnostics"]["skipped_no_image"], 1)
        self.assertEqual(result["card_diagnostics"]["missing_sample"], [])

    def test_woocommerce_shop_filter_category_uses_store_api_category(self):
        category_response = MagicMock()
        category_response.is_error = False
        category_response.json.return_value = [
            {"id": 107, "slug": "af1", "count": 20069}
        ]
        first_response = MagicMock()
        first_response.is_error = False
        first_response.headers = {"x-wp-total": "20069"}
        first_response.json.return_value = [
            {
                "id": 10,
                "name": "AF1 Shoe A",
                "permalink": "https://nailedbygeo.com/product/af1-a/",
                "prices": {
                    "price": "7895",
                    "regular_price": "7895",
                    "currency_code": "USD",
                    "currency_minor_unit": 2,
                },
                "images": [{"src": "https://cdn.example/a.jpg"}],
            },
            {
                "id": 11,
                "name": "AF1 Shoe B",
                "permalink": "https://nailedbygeo.com/product/af1-b/",
                "prices": {
                    "price": "7895",
                    "regular_price": "7895",
                    "currency_code": "USD",
                    "currency_minor_unit": 2,
                },
                "images": [{"src": "https://cdn.example/b.jpg"}],
            },
        ]
        client = MagicMock()
        client.get.side_effect = [category_response, first_response]

        result = firecrawl_client._discover_woocommerce_collection_products(
            client,
            "https://nailedbygeo.com/shop/?orderby=popularity&filter_category=af1",
            max_products=2,
            strict=True,
        )

        self.assertEqual(result["advertised_count"], 20069)
        self.assertEqual(len(result["urls"]), 2)
        self.assertTrue(result["truncated"])
        self.assertEqual(client.get.call_args_list[0].kwargs["params"]["search"], "af1")
        self.assertEqual(client.get.call_args_list[1].kwargs["params"]["category"], 107)

    def test_printerval_search_follows_next_pages_and_validates_product_ids(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {
                    "javascriptReturns": [{
                        "value": {
                            "advertised_count": 3,
                            "pages_scanned": 2,
                            "links": [
                                "https://printerval.com/item-a-p100?ref=search",
                                "https://printerval.com/item-b-p200",
                                "https://printerval.com/uk/item-c-p300",
                            ],
                            "products": [
                                {
                                    "title": "Beer Sweater A",
                                    "price": 26.95,
                                    "compare_at_price": 39.92,
                                    "currency": "USD",
                                    "product_url": "https://printerval.com/item-a-p100?ref=search",
                                    "images": [
                                        "https://cdn.example/a-front.jpg",
                                        "https://cdn.example/a-back.jpg",
                                        "https://cdn.example/a-third.jpg",
                                    ],
                                },
                                {
                                    "title": "Beer Sweater B",
                                    "price": 27.95,
                                    "currency": "USD",
                                    "product_url": "https://printerval.com/item-b-p200",
                                    "images": ["https://cdn.example/b.jpg"],
                                },
                                {
                                    "title": "Beer Sweater C",
                                    "price": 28.95,
                                    "currency": "USD",
                                    "product_url": "https://printerval.com/uk/item-c-p300",
                                    "images": ["https://cdn.example/c.jpg"],
                                },
                            ],
                        }
                    }]
                },
            }
        }
        client = MagicMock()
        client.post.return_value = response

        result = firecrawl_client._discover_printerval_search_product_links(
            client,
            "https://printerval.com/search?q=beer+sweater&page=8",
            domain="printerval.com",
            path_hint="/products/",
            max_products=10_000,
        )

        self.assertEqual(len(result["urls"]), 3)
        self.assertEqual(result["advertised_count"], 3)
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(result["discovery_mode"], "printerval_search")
        self.assertEqual(len(result["products"]), 3)
        self.assertEqual(len(result["products"][0]["images"]), 2)
        payload = client.post.call_args.kwargs["json"]
        self.assertNotIn("page=8", payload["url"])
        script = payload["actions"][1]["script"]
        self.assertIn("Showing", script.replace("showing", "Showing"))
        self.assertIn("iframe", script)
        self.assertIn("nextUrl", script)
        self.assertIn("-p(\\d+)", script)
        self.assertIn("function productCard", script)
        self.assertIn("productIdsWithin", script)
        self.assertIn("[data-product-price]", script)
        self.assertIn("product.price != null", script)
        self.assertIn("heart|avatar|logo|icon", script)
        self.assertIn("\\.svg", script)

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client, "_discover_printerval_search_product_links")
    @patch.object(firecrawl_client.httpx, "Client")
    def test_printerval_search_routes_to_platform_adapter(
        self, client_cls, printerval_discovery, _check_key
    ):
        printerval_discovery.return_value = {
            "urls": ["https://printerval.com/item-p100"],
            "pages_scanned": 18,
            "credits_used": 1,
            "truncated": False,
            "discovery_mode": "printerval_search",
            "advertised_count": 1049,
        }

        result = firecrawl_client.discover_collection_product_links(
            "https://printerval.com/search?q=beer+sweater"
        )

        self.assertEqual(result["advertised_count"], 1049)
        self.assertEqual(result["discovery_mode"], "printerval_search")
        printerval_discovery.assert_called_once()

    def test_sfcc_search_uses_only_product_grid_and_validates_total(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {
                    "javascriptReturns": [{
                        "type": "object",
                        "value": {
                            "advertised_count": 3,
                            "pages_scanned": 1,
                            "recovery_passes": 1,
                            "links": [
                                "https://www.boxlunch.com/product/item-a/100.html?cgid=x",
                                "https://www.boxlunch.com/product/item-a/100.html?cgid=x",
                                "https://www.boxlunch.com/product/item-b/200.html",
                                "https://www.boxlunch.com/product/item-c/300.html",
                            ],
                        },
                    }]
                },
            }
        }
        client = MagicMock()
        client.post.return_value = response

        result = firecrawl_client._discover_sfcc_search_product_links(
            client,
            "https://www.boxlunch.com/search?q=mickey&page=1",
            domain="www.boxlunch.com",
            path_hint="/products/",
            max_products=10,
        )

        self.assertEqual(
            result["urls"],
            [
                "https://www.boxlunch.com/product/item-a/100.html",
                "https://www.boxlunch.com/product/item-b/200.html",
                "https://www.boxlunch.com/product/item-c/300.html",
            ],
        )
        self.assertEqual(result["advertised_count"], 3)
        self.assertEqual(result["recovery_passes"], 1)
        self.assertEqual(result["discovery_mode"], "sfcc_search")
        self.assertTrue(result["waf_blocked"])
        payload = client.post.call_args.kwargs["json"]
        self.assertIn("start=0", payload["url"])
        self.assertIn("sz=48", payload["url"])
        script = payload["actions"][1]["script"]
        self.assertIn(".product-grid", script)
        self.assertIn("uniqueLinks.size < required", script)
        self.assertIn("nextBatchUrl", script)
        self.assertIn("cache: 'no-store'", script)

    def test_sfcc_search_refuses_a_partial_product_grid(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "actions": {
                    "javascriptReturns": [{
                        "value": {
                            "advertised_count": 485,
                            "pages_scanned": 11,
                            "links": [
                                "https://www.boxlunch.com/product/item-a/100.html",
                            ],
                        },
                    }]
                }
            }
        }
        client = MagicMock()
        client.post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "product-grid"):
            firecrawl_client._discover_sfcc_search_product_links(
                client,
                "https://www.boxlunch.com/search?q=mickey",
                domain="www.boxlunch.com",
                path_hint="/products/",
                max_products=1000,
            )

    def test_wordpress_search_reads_all_advertised_product_urls(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {
                    "javascriptReturns": [{
                        "value": {
                            "advertised_count": 3,
                            "pages_scanned": 2,
                            "links": [
                                "https://teehobbies.us/product/hoodie-a/?attribute=x",
                                "https://teehobbies.us/product/hoodie-a/",
                                "https://teehobbies.us/product/hoodie-b/",
                                "https://teehobbies.us/product/hoodie-c/",
                            ],
                        },
                    }]
                },
            }
        }
        client = MagicMock()
        client.post.return_value = response

        result = firecrawl_client._discover_wordpress_search_product_links(
            client,
            "https://teehobbies.us/?s=mickey+hoodie&paged=8",
            domain="teehobbies.us",
            path_hint="/products/",
            max_products=1000,
        )

        self.assertEqual(len(result["urls"]), 3)
        self.assertEqual(result["advertised_count"], 3)
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(result["discovery_mode"], "wordpress_search")
        payload = client.post.call_args.kwargs["json"]
        self.assertNotIn("paged=8", payload["url"])
        script = payload["actions"][1]["script"]
        self.assertIn("a.next.page-numbers", script)
        self.assertIn("searchParams.set('paged'", script)
        self.assertIn("ul.products li.product", script)
        self.assertIn("join('\\n')", script)
        self.assertNotIn("join('\n')", script)

    def test_wordpress_search_resets_pretty_pagination(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {
                    "javascriptReturns": [{
                        "value": {
                            "advertised_count": 1,
                            "pages_scanned": 1,
                            "links": [
                                "https://royaljacket.com/product/varsity-jacket/",
                            ],
                        },
                    }]
                },
            }
        }
        client = MagicMock()
        client.post.return_value = response

        result = firecrawl_client._discover_wordpress_search_product_links(
            client,
            "https://royaljacket.com/page/15/?s=Varsity+jacket&post_type=product",
            domain="royaljacket.com",
            path_hint="/product/",
            max_products=1000,
        )

        self.assertEqual(len(result["urls"]), 1)
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(
            payload["url"],
            "https://royaljacket.com/?s=Varsity+jacket&post_type=product",
        )
        script = payload["actions"][1]["script"]
        self.assertIn("page/${page}/", script)
        self.assertIn("WordPress search ${targetUrl} HTTP ${response.status}", script)

    def test_wordpress_search_uses_discovered_count_after_exhausting_pagination(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {
                    "javascriptReturns": [{
                        "value": {
                            "advertised_count": 0,
                            "pages_scanned": 2,
                            "scan_complete": True,
                            "links": [
                                "https://royaljacket.com/product/varsity-a/",
                                "https://royaljacket.com/product/varsity-b/",
                            ],
                        },
                    }]
                },
            }
        }
        client = MagicMock()
        client.post.return_value = response

        result = firecrawl_client._discover_wordpress_search_product_links(
            client,
            "https://royaljacket.com/?s=Varsity+jacket&post_type=product",
            domain="royaljacket.com",
            path_hint="/product/",
            max_products=1000,
        )

        self.assertEqual(result["advertised_count"], 2)
        self.assertEqual(len(result["urls"]), 2)
        self.assertFalse(result["truncated"])

    def test_wordpress_search_rejects_unknown_count_before_pagination_finishes(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "actions": {
                    "javascriptReturns": [{
                        "value": {
                            "advertised_count": 0,
                            "pages_scanned": 1,
                            "scan_complete": False,
                            "links": [
                                "https://royaljacket.com/product/varsity-a/",
                            ],
                        },
                    }]
                },
            }
        }
        client = MagicMock()
        client.post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "phân trang"):
            firecrawl_client._discover_wordpress_search_product_links(
                client,
                "https://royaljacket.com/?s=Varsity+jacket&post_type=product",
                domain="royaljacket.com",
                path_hint="/product/",
                max_products=1000,
            )

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client, "_discover_wordpress_search_product_links")
    @patch.object(firecrawl_client.httpx, "Client")
    def test_wordpress_query_routes_to_search_adapter(
        self, client_cls, wordpress_discovery, _check_key
    ):
        wordpress_discovery.return_value = {
            "urls": ["https://teehobbies.us/product/a/"],
            "pages_scanned": 18,
            "credits_used": 1,
            "truncated": False,
            "discovery_mode": "wordpress_search",
            "advertised_count": 206,
        }

        result = firecrawl_client.discover_collection_product_links(
            "https://teehobbies.us/?s=mickey+hoodie"
        )

        self.assertEqual(result["advertised_count"], 206)
        self.assertEqual(result["discovery_mode"], "wordpress_search")
        wordpress_discovery.assert_called_once()
        called_url = wordpress_discovery.call_args.args[1]
        self.assertEqual(called_url, "https://teehobbies.us/?s=mickey+hoodie")

    def test_sitebase_search_api_fetches_all_pages_with_stable_sort(self):
        first_response = MagicMock()
        first_response.is_error = False
        first_response.json.return_value = {
            "code": 0,
            "result": {
                "count": 4,
                "items": [
                    {"handle": "a", "title": "A", "price": 10, "images": [{"src": "https://cdn.example/a.jpg"}]},
                    {"handle": "b", "title": "B", "price": 20, "images": [{"src": "https://cdn.example/b.jpg"}]},
                ],
            },
        }
        second_response = MagicMock()
        second_response.is_error = False
        second_response.json.return_value = {
            "code": 0,
            "result": {
                "count": 4,
                "items": [
                    {"handle": "c", "title": "C", "price": 30, "images": [{"src": "https://cdn.example/c.jpg"}]},
                    {"handle": "d", "title": "D", "price": 40, "images": [{"src": "https://cdn.example/d.jpg"}]},
                ],
            },
        }
        client = MagicMock()
        client.get.side_effect = [first_response, second_response]

        with patch.object(firecrawl_client, "SEARCH_API_PAGE_SIZE", 2):
            result = firecrawl_client._discover_sitebase_search_product_links(
                client,
                "https://shop.example/search?q=hoodie&page=1",
                domain="shop.example",
                path_hint="/products/",
                max_products=10,
            )

        self.assertEqual(
            result["urls"],
            [
                "https://shop.example/products/a",
                "https://shop.example/products/b",
                "https://shop.example/products/c",
                "https://shop.example/products/d",
            ],
        )
        self.assertEqual(result["advertised_count"], 4)
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(result["credits_used"], 0)
        self.assertEqual(result["discovery_mode"], "search_api")
        self.assertFalse(result["waf_blocked"])

        first_params = dict(client.get.call_args_list[0].kwargs["params"])
        second_params = dict(client.get.call_args_list[1].kwargs["params"])
        self.assertEqual(first_params["sort_field"], "created")
        self.assertEqual(first_params["infinite"], "false")
        self.assertEqual(second_params["page"], "2")
        self.assertEqual(second_params["infinite"], "true")

    def test_sitebase_search_accepts_distinct_ids_that_share_one_handle(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "code": 0,
            "result": {
                "count": 3,
                "items": [
                    {"id": 101, "handle": "duplicate-handle"},
                    {"id": 102, "handle": "duplicate-handle"},
                    {"id": 103, "handle": "unique-handle"},
                ],
            },
        }
        client = MagicMock()
        client.get.return_value = response

        with patch.object(firecrawl_client, "SEARCH_API_PAGE_SIZE", 3):
            result = firecrawl_client._discover_sitebase_search_product_links(
                client,
                "https://shop.example/search?q=shoes",
                domain="shop.example",
                path_hint="/products/",
                max_products=10,
            )

        self.assertEqual(
            result["urls"],
            [
                "https://shop.example/products/duplicate-handle",
                "https://shop.example/products/unique-handle",
            ],
        )
        self.assertEqual(result["advertised_count"], 3)
        self.assertEqual(result["duplicate_url_count"], 1)
        self.assertEqual(result["recovery_passes"], 0)

    def test_sitebase_search_retries_duplicate_id_at_page_boundary(self):
        def api_response(items):
            response = MagicMock()
            response.is_error = False
            response.json.return_value = {
                "code": 0,
                "result": {"count": 4, "items": items},
            }
            return response

        client = MagicMock()
        client.get.side_effect = [
            api_response([{"id": 1, "handle": "a"}, {"id": 2, "handle": "b"}]),
            api_response([{"id": 2, "handle": "b"}, {"id": 3, "handle": "c"}]),
            api_response([{"id": 1, "handle": "a"}, {"id": 2, "handle": "b"}]),
            api_response([{"id": 3, "handle": "c"}, {"id": 4, "handle": "d"}]),
        ]

        with patch.object(firecrawl_client, "SEARCH_API_PAGE_SIZE", 2):
            result = firecrawl_client._discover_sitebase_search_product_links(
                client,
                "https://shop.example/search?q=shoes",
                domain="shop.example",
                path_hint="/products/",
                max_products=10,
            )

        self.assertEqual(
            result["urls"],
            [
                "https://shop.example/products/a",
                "https://shop.example/products/b",
                "https://shop.example/products/c",
                "https://shop.example/products/d",
            ],
        )
        self.assertEqual(result["recovery_passes"], 1)
        self.assertEqual(result["duplicate_url_count"], 0)
        self.assertEqual(client.get.call_count, 4)

    def test_shopbase_collection_api_fetches_all_products_and_card_images(self):
        collection_response = MagicMock()
        collection_response.status_code = 200
        collection_response.is_error = False
        collection_response.json.return_value = {
            "code": 0,
            "result": {
                "count": 1,
                "items": [{
                    "id": 86820980638,
                    "handle": "halloween",
                    "sort_order": "created:desc",
                }],
            },
        }
        first_response = MagicMock()
        first_response.status_code = 200
        first_response.is_error = False
        first_response.json.return_value = {
            "code": 0,
            "result": {
                "count": 3,
                "items": [
                    {
                        "handle": "a",
                        "title": "A",
                        "price": 10,
                        "min_price_compare": 15,
                        "images": [
                            {"src": "https://cdn.example/a-front.jpg"},
                            {"src": "https://cdn.example/a-back.jpg"},
                        ],
                    },
                    {
                        "handle": "b",
                        "title": "B",
                        "price": 20,
                        "images": [{"src": "https://cdn.example/b.jpg"}],
                    },
                ],
            },
        }
        second_response = MagicMock()
        second_response.status_code = 200
        second_response.is_error = False
        second_response.json.return_value = {
            "code": 0,
            "result": {
                "count": 3,
                "items": [{
                    "handle": "c",
                    "title": "C",
                    "price": 30,
                    "images": [{"src": "https://cdn.example/c.jpg"}],
                }],
            },
        }
        client = MagicMock()
        client.get.side_effect = [
            collection_response,
            first_response,
            second_response,
        ]

        with patch.object(firecrawl_client, "SEARCH_API_PAGE_SIZE", 2):
            result = firecrawl_client._discover_sitebase_collection_products(
                client,
                "https://pumpbear.com/collections/halloween",
                domain="pumpbear.com",
                path_hint="/products/",
                max_products=10,
            )

        self.assertEqual(len(result["urls"]), 3)
        self.assertEqual(result["advertised_count"], 3)
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(result["credits_used"], 0)
        self.assertEqual(result["discovery_mode"], "shopbase_collection_api")
        self.assertFalse(result["full_gallery_available"])
        self.assertEqual(
            result["products"][0]["images"],
            [
                "https://cdn.example/a-front.jpg",
                "https://cdn.example/a-back.jpg",
            ],
        )
        first_params = dict(client.get.call_args_list[1].kwargs["params"])
        second_params = dict(client.get.call_args_list[2].kwargs["params"])
        self.assertEqual(first_params["collection_ids"], "86820980638")
        self.assertEqual(first_params["infinite"], "false")
        self.assertEqual(second_params["page"], "2")
        self.assertEqual(second_params["infinite"], "true")

    def test_shopbase_collection_api_declines_an_unrelated_storefront(self):
        response = MagicMock()
        response.status_code = 404
        response.is_error = True
        client = MagicMock()
        client.get.return_value = response

        result = firecrawl_client._discover_sitebase_collection_products(
            client,
            "https://shop.example/collections/chairs",
            domain="shop.example",
            path_hint="/products/",
            max_products=10,
        )

        self.assertIsNone(result)

    @patch.object(firecrawl_client, "_check_key")
    def test_auto_detected_sitebase_search_marks_shopbase_platform(self, _check_key):
        sitebase_result = {
            "urls": ["https://pumpbear.com/products/a"],
            "pages_scanned": 1,
            "credits_used": 0,
            "truncated": False,
            "discovery_mode": "search_api",
            "waf_blocked": True,
        }
        with (
            patch.object(firecrawl_client.httpx, "Client"),
            patch.object(
                firecrawl_client,
                "_discover_woocommerce_collection_products",
                return_value=None,
            ),
            patch.object(
                firecrawl_client,
                "_discover_sitebase_collection_products",
                return_value=None,
            ),
            patch.object(
                firecrawl_client,
                "_discover_printerval_search_product_links",
                return_value=None,
            ),
            patch.object(
                firecrawl_client,
                "_discover_wordpress_search_product_links",
                return_value=None,
            ),
            patch.object(
                firecrawl_client,
                "_discover_sfcc_search_product_links",
                return_value=None,
            ),
            patch.object(
                firecrawl_client,
                "_discover_sitebase_search_product_links",
                return_value=sitebase_result,
            ),
        ):
            result = firecrawl_client.discover_collection_product_links(
                "https://sitebase.example/search?q=hoodie"
            )

        self.assertEqual(result["platform"], "shopbase")

    @patch.object(firecrawl_client, "_check_key")
    def test_explicit_shopbase_routes_new_pumpbear_search_to_html_adapter(self, _check_key):
        pumpbear_result = {
            "urls": ["https://pumpbear.com/products/a"],
            "products": [],
            "pages_scanned": 1,
            "credits_used": 1,
            "truncated": False,
            "discovery_mode": "pumpbear_search_html",
            "advertised_count": 1,
        }
        with (
            patch.object(firecrawl_client.httpx, "Client"),
            patch.object(
                firecrawl_client,
                "_discover_pumpbear_search_product_links",
                return_value=pumpbear_result,
            ) as pumpbear_discovery,
            patch.object(
                firecrawl_client,
                "_discover_sitebase_search_product_links",
            ) as sitebase_discovery,
        ):
            result = firecrawl_client.discover_collection_product_links(
                "https://pumpbear.com/search?page=2&q=touchdown",
                platform="shopbase",
            )

        self.assertEqual(result["platform"], "shopbase")
        pumpbear_discovery.assert_called_once()
        sitebase_discovery.assert_not_called()

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client.httpx, "Client")
    @patch.object(firecrawl_client, "_discover_shopify_collection_via_browser")
    @patch.object(firecrawl_client, "_discover_sitebase_collection_products")
    def test_explicit_shopbase_platform_never_falls_through_to_shopify(
        self, sitebase_discovery, shopify_discovery, client_cls, _check_key
    ):
        sitebase_discovery.return_value = {
            "urls": ["https://pumpbear.com/products/a"],
            "pages_scanned": 1,
            "credits_used": 0,
            "truncated": False,
            "discovery_mode": "shopbase_collection_api",
        }

        result = firecrawl_client.discover_collection_product_links(
            "https://pumpbear.com/collections/arizona-cardinals",
            platform="shopbase",
        )

        self.assertEqual(result["platform"], "shopbase")
        self.assertTrue(sitebase_discovery.call_args.kwargs["strict"])
        shopify_discovery.assert_not_called()

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client.httpx, "Client")
    @patch.object(firecrawl_client, "_discover_shopify_collection_via_browser")
    @patch.object(firecrawl_client, "_discover_sitebase_collection_via_browser")
    @patch.object(firecrawl_client, "_discover_sitebase_collection_products")
    def test_explicit_shopbase_uses_shopbase_browser_fallback_only(
        self,
        sitebase_direct,
        sitebase_browser,
        shopify_discovery,
        client_cls,
        _check_key,
    ):
        sitebase_direct.side_effect = RuntimeError("direct API blocked")
        sitebase_browser.return_value = {
            "urls": ["https://pumpbear.com/products/a"],
            "pages_scanned": 14,
            "credits_used": 1,
            "truncated": False,
            "discovery_mode": "shopbase_collection_api",
        }

        result = firecrawl_client.discover_collection_product_links(
            "https://pumpbear.com/collections/arizona-cardinals",
            platform="shopbase",
        )

        self.assertEqual(result["platform"], "shopbase")
        sitebase_browser.assert_called_once()
        shopify_discovery.assert_not_called()

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client.httpx, "Client")
    @patch.object(firecrawl_client, "_discover_shopify_collection_via_browser")
    @patch.object(firecrawl_client, "_discover_sitebase_collection_products")
    def test_explicit_shopify_platform_never_probes_shopbase(
        self, sitebase_discovery, shopify_discovery, client_cls, _check_key
    ):
        shopify_discovery.return_value = {
            "urls": ["https://shop.example/products/a"],
            "pages_scanned": 1,
            "credits_used": 1,
            "truncated": False,
            "discovery_mode": "shopify_collection_api",
        }

        result = firecrawl_client.discover_collection_product_links(
            "https://shop.example/collections/hoodies",
            platform="shopify",
        )

        self.assertEqual(result["platform"], "shopify")
        shopify_discovery.assert_called_once()
        sitebase_discovery.assert_not_called()

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client.httpx, "Client")
    @patch.object(firecrawl_client, "_discover_shopify_collection_via_browser")
    @patch.object(firecrawl_client, "_discover_shopify_search_product_links")
    def test_explicit_shopify_search_uses_html_pagination(
        self,
        search_discovery,
        collection_discovery,
        client_cls,
        _check_key,
    ):
        search_discovery.return_value = {
            "urls": ["https://shop.example/products/a"],
            "pages_scanned": 1,
            "credits_used": 0,
            "truncated": False,
            "discovery_mode": "shopify_search_html",
        }

        result = firecrawl_client.discover_collection_product_links(
            "https://shop.example/search?q=sweater&page=2",
            platform="shopify",
        )

        self.assertEqual(result["platform"], "shopify")
        self.assertTrue(search_discovery.call_args.kwargs["strict"])
        self.assertIn(
            "page=1",
            search_discovery.call_args.args[1],
        )
        collection_discovery.assert_not_called()

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client.httpx, "Client")
    @patch.object(firecrawl_client, "_discover_shopify_collection_via_browser")
    @patch.object(firecrawl_client, "_discover_sitebase_collection_products")
    @patch.object(firecrawl_client, "_discover_woocommerce_collection_products")
    def test_explicit_woocommerce_platform_uses_only_store_api(
        self,
        woocommerce_discovery,
        sitebase_discovery,
        shopify_discovery,
        client_cls,
        _check_key,
    ):
        woocommerce_discovery.return_value = {
            "urls": ["https://nailedbygeo.com/product/a/"],
            "pages_scanned": 1,
            "credits_used": 0,
            "truncated": False,
            "discovery_mode": "woocommerce_store_api",
        }

        result = firecrawl_client.discover_collection_product_links(
            "https://nailedbygeo.com/shop/?filter_category=af1",
            platform="woocommerce",
        )

        self.assertEqual(result["platform"], "woocommerce")
        self.assertTrue(woocommerce_discovery.call_args.kwargs["strict"])
        sitebase_discovery.assert_not_called()
        shopify_discovery.assert_not_called()

    @patch.object(firecrawl_client, "_post_scrape_with_retry")
    def test_shopbase_browser_fallback_returns_complete_card_batch(self, post_scrape):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {"javascriptReturns": [{"value": {
                    "advertised_count": 2,
                    "pages_scanned": 1,
                    "products": [
                        {
                            "handle": "a",
                            "title": "A",
                            "price": 10,
                            "images": [{"src": "https://cdn.example/a.jpg"}],
                        },
                        {
                            "handle": "b",
                            "title": "B",
                            "price": 20,
                            "images": [{"src": "https://cdn.example/b.jpg"}],
                        },
                    ],
                }}]},
            },
        }
        post_scrape.return_value = response

        result = firecrawl_client._discover_sitebase_collection_via_browser(
            MagicMock(),
            "https://pumpbear.com/collections/arizona-cardinals",
            domain="pumpbear.com",
            path_hint="/products/",
            max_products=10,
        )

        self.assertEqual(len(result["urls"]), 2)
        self.assertEqual(result["advertised_count"], 2)
        self.assertEqual(result["credits_used"], 1)
        payload = post_scrape.call_args.args[1]
        self.assertIn("collection_ids", payload["actions"][1]["script"])

    def test_sitebase_search_api_declines_an_unrelated_storefront(self):
        response = MagicMock()
        response.is_error = True
        client = MagicMock()
        client.get.return_value = response

        result = firecrawl_client._discover_sitebase_search_product_links(
            client,
            "https://shop.example/search?q=hoodie",
            domain="shop.example",
            path_hint="/products/",
            max_products=10,
        )

        self.assertIsNone(result)

    @patch.object(firecrawl_client, "_discover_sitebase_search_via_browser")
    def test_explicit_shopbase_search_uses_browser_when_catalogue_api_is_blocked(
        self, browser_discovery
    ):
        blocked_response = MagicMock()
        blocked_response.is_error = True
        blocked_response.status_code = 403
        blocked_response.text = "Forbidden"
        client = MagicMock()
        client.get.return_value = blocked_response
        browser_discovery.return_value = {
            "urls": ["https://shop.example/products/a"],
            "advertised_count": 1,
            "pages_scanned": 1,
            "credits_used": 1,
        }

        result = firecrawl_client._discover_sitebase_search_product_links(
            client,
            "https://shop.example/search?q=hoodie",
            domain="shop.example",
            path_hint="/products/",
            max_products=10,
            strict=True,
        )

        self.assertEqual(result["advertised_count"], 1)
        browser_discovery.assert_called_once()

    def test_sitebase_search_uses_browser_api_when_security_page_blocks_http(self):
        blocked_response = MagicMock()
        blocked_response.is_error = False
        blocked_response.status_code = 200
        blocked_response.text = "<title>Security Checking</title>"
        blocked_response.json.side_effect = ValueError("not json")

        browser_response = MagicMock()
        browser_response.is_error = False
        browser_response.status_code = 200
        browser_response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {
                    "javascriptReturns": [{
                        "type": "object",
                        "value": {
                            "advertised_count": 3,
                            "pages_scanned": 1,
                            "records": [
                                {
                                    "id": 1,
                                    "handle": "a",
                                    "title": "A",
                                    "price": 10,
                                    "images": [{"src": "https://cdn.example/a.jpg"}],
                                },
                                {
                                    "id": 2,
                                    "handle": "b",
                                    "title": "B",
                                    "price": 20,
                                    "images": [],
                                },
                                {
                                    "id": 3,
                                    "handle": "c",
                                    "title": "C",
                                    "price": 30,
                                    "images": [{"src": "https://cdn.example/c.jpg"}],
                                },
                            ],
                            "handles": ["a", "b", "c"],
                        },
                    }]
                },
            }
        }
        client = MagicMock()
        client.get.return_value = blocked_response
        client.post.return_value = browser_response

        result = firecrawl_client._discover_sitebase_search_product_links(
            client,
            "https://shop.example/search?q=hoodie&page=1",
            domain="shop.example",
            path_hint="/products/",
            max_products=10,
        )

        self.assertEqual(len(result["urls"]), 3)
        self.assertEqual(result["advertised_count"], 3)
        self.assertEqual(result["credits_used"], 1)
        self.assertEqual(result["discovery_mode"], "search_api")
        self.assertTrue(result["waf_blocked"])
        self.assertEqual(len(result["products"]), 3)
        self.assertEqual(result["card_diagnostics"]["with_images"], 2)
        self.assertEqual(result["products"][1]["images"], [])
        action_payload = client.post.call_args.kwargs["json"]
        script = action_payload["actions"][1]["script"]
        self.assertIn("sort_field', 'created", script)
        self.assertIn("Promise.all", script)
        self.assertIn("records: items.map", script)
        self.assertIn("images.slice(0, 2)", script)
        self.assertIn("https://shop.example/search?q=hoodie&page=1", script)

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client, "_post_scrape_with_retry")
    def test_sitebase_grouped_browser_gallery_recovers_full_product(
        self, post_scrape, _check_key
    ):
        product_url = "https://pumpbear.com/products/full-gallery-hoodie"
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "data": {
                "metadata": {"creditsUsed": 1},
                "actions": {
                    "javascriptReturns": [{
                        "value": {
                            "products": [{
                                "title": "Full Gallery Hoodie",
                                "price": 59.95,
                                "compare_at_price": 69.95,
                                "currency": "USD",
                                "product_url": product_url,
                                "images": [
                                    "https://img.thesitebase.net/products/ver_1/front.jpg",
                                    "https://img.thesitebase.net/products/ver_1/back.jpg",
                                ],
                            }],
                            "failed_urls": [],
                        },
                    }],
                },
            },
        }
        post_scrape.return_value = response

        result = firecrawl_client.scrape_sitebase_product_galleries_via_browser(
            "https://pumpbear.com/search?q=hoodie",
            [product_url],
        )

        self.assertEqual(result["failed_urls"], [])
        self.assertEqual(result["credits_used"], 1)
        self.assertEqual(len(result["products_by_url"][product_url]["images"]), 2)
        payload = post_scrape.call_args.args[1]
        script = payload["actions"][1]["script"]
        self.assertIn(product_url, script)
        self.assertIn("Promise.all", script)

    def test_infinite_scroll_refuses_incomplete_advertised_result(self):
        initial_response = MagicMock()
        initial_response.is_error = False
        initial_response.json.return_value = {
            "data": {
                "markdown": "3 Products",
                "links": ["https://shop.example/products/a"],
            }
        }
        scrolled_response = MagicMock()
        scrolled_response.is_error = False
        scrolled_response.json.return_value = {
            "data": {"links": ["https://shop.example/products/a"]}
        }
        client = MagicMock()
        client.post.side_effect = [initial_response, scrolled_response]

        with self.assertRaisesRegex(RuntimeError, "công bố 3 sản phẩm"):
            firecrawl_client._discover_infinite_scroll_product_links(
                client,
                "https://shop.example/search?q=chair",
                domain="shop.example",
                path_hint="/products/",
                max_products=10,
            )

    def test_normalises_images_and_skips_failed_pages(self):
        pages = [
            {
                "json": {
                    "title": "Chair",
                    "price": 49.5,
                    "images": ["/cdn/chair-1.jpg", "/cdn/chair-1.jpg", "https://cdn.example/chair-2.jpg"],
                },
                "metadata": {"statusCode": 200, "sourceURL": "https://shop.example/products/chair"},
                "markdown": "Chair 49.50 49.50",
            },
            {
                "json": {"title": "Invented", "price": 1},
                "metadata": {"statusCode": 404},
            },
        ]

        self.assertEqual(
            firecrawl_client._normalise_collection_products(pages),
            [{
                "title": "Chair",
                "price": 49.5,
                "compare_at_price": None,
                "currency": "",
                "product_url": "https://shop.example/products/chair",
                "images": [
                    "https://shop.example/cdn/chair-1.jpg",
                    "https://cdn.example/chair-2.jpg",
                ],
            }],
        )

    @patch.object(firecrawl_client, "_check_key")
    @patch.object(firecrawl_client.httpx, "Client")
    def test_collection_batch_is_async_and_not_capped_at_fifty(self, client_cls, _check_key):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {"id": "batch-123", "invalidURLs": []}
        client_cls.return_value.__enter__.return_value.post.return_value = response
        urls = [f"https://shop.example/products/{index}" for index in range(75)]

        started = firecrawl_client.start_collection_product_export(urls)

        self.assertEqual(started["job_id"], "batch-123")
        self.assertEqual(started["requested"], 75)
        payload = client_cls.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        self.assertEqual(len(payload["urls"]), 75)
        self.assertEqual(payload["formats"], ["markdown"])
        self.assertTrue(payload["onlyMainContent"])
        self.assertEqual(
            payload["maxAge"], firecrawl_client.COLLECTION_CACHE_MAX_AGE_MS
        )
        self.assertEqual(payload["actions"][0], {"type": "wait", "milliseconds": 500})
        self.assertEqual(payload["actions"][1]["type"], "executeJavascript")
        self.assertIn("data-src-zoom-image", payload["actions"][1]["script"])
        self.assertIn("data-a-dynamic-image", payload["actions"][1]["script"])
        self.assertIn("shopify.price", payload["actions"][1]["script"])
        self.assertIn("thumbnail-carousel-slide", payload["actions"][1]["script"])
        self.assertIn("ProductGroup", payload["actions"][1]["script"])
        self.assertIn(".product-images img.product-thumbnail-image", payload["actions"][1]["script"])
        self.assertIn("cdn\\.media\\.amplience\\.net", payload["actions"][1]["script"])

    def test_completed_label_waits_for_numeric_total(self):
        self.assertEqual(
            firecrawl_client._collection_batch_progress({
                "status": "completed",
                "completed": 74,
                "total": 412,
                "data": [{}] * 74,
            }),
            ("processing", 74, 412),
        )
        self.assertEqual(
            firecrawl_client._collection_batch_progress({
                "status": "completed",
                "completed": 412,
                "total": 412,
            }),
            ("completed", 412, 412),
        )

    def test_normalises_small_browser_action_result_with_full_gallery(self):
        pages = [{
            "metadata": {
                "statusCode": 200,
                "sourceURL": "https://shop.example/products/hoodie",
            },
            "actions": {
                "javascriptReturns": [{
                    "type": "object",
                    "value": {
                        "title": "Gallery Hoodie",
                        "price": "46.95",
                        "compare_at_price": "59.95",
                        "currency": "USD",
                        "product_url": "https://shop.example/products/hoodie",
                        "images": [
                            "https://cdn.example/front.jpg",
                            "https://cdn.example/back.jpg",
                            "https://cdn.example/front.jpg",
                        ],
                    },
                }],
            },
        }]

        self.assertEqual(
            firecrawl_client._normalise_collection_products(pages),
            [{
                "title": "Gallery Hoodie",
                "price": "46.95",
                "compare_at_price": "59.95",
                "currency": "USD",
                "product_url": "https://shop.example/products/hoodie",
                "images": [
                    "https://cdn.example/front.jpg",
                    "https://cdn.example/back.jpg",
                ],
            }],
        )

    def test_normalises_shopbase_action_result_without_ui_assets(self):
        pages = [{
            "metadata": {
                "statusCode": 200,
                "sourceURL": "https://pumpbear.com/products/bluey-hoodie",
            },
            "actions": {"javascriptReturns": [{"value": {
                "title": "Bluey Hoodie",
                "price": "46.95",
                "currency": "USD",
                "images": [
                    "https://img.thesitebase.net/img/flags/usa.png",
                    "https://img.thesitebase.net/store/themes/logo.png",
                    "https://img.thesitebase.net/store/products/ver_1/front.jpeg",
                    "https://img.thesitebase.net/store/products/ver_1/back.jpeg",
                    "https://img.thesitebase.net/store/products/ver_1/front.jpeg?min_height=0",
                    "https://img.thesitebase.net/store/products/related.jpeg",
                ],
            }}]},
        }]

        product = firecrawl_client._normalise_collection_products(pages)[0]

        self.assertEqual(
            product["images"],
            [
                "https://img.thesitebase.net/store/products/ver_1/front.jpeg",
                "https://img.thesitebase.net/store/products/ver_1/back.jpeg",
            ],
        )

    def test_normalises_html_entities_in_etsy_title(self):
        pages = [{
            "metadata": {
                "statusCode": 200,
                "sourceURL": "https://www.etsy.com/listing/1/hoodie",
            },
            "actions": {"javascriptReturns": [{"value": {
                "title": "Mother&#39;s Day Hoodie",
                "price": "29.00",
                "currency": "USD",
                "product_url": "https://www.etsy.com/listing/1/hoodie",
                "images": ["https://i.etsystatic.com/1/il_fullxfull.jpg"],
            }}]},
        }]

        self.assertEqual(
            firecrawl_client._normalise_collection_products(pages)[0]["title"],
            "Mother's Day Hoodie",
        )


class CollectionFastClientTests(SimpleTestCase):
    def test_fast_path_rejects_private_and_credentialed_urls(self):
        self.assertFalse(collection_fast_client._is_public_http_url("http://127.0.0.1/product"))
        self.assertFalse(collection_fast_client._is_public_http_url("http://[::1]/product"))
        self.assertFalse(collection_fast_client._is_public_http_url("https://user:pass@example.com/product"))

    def test_shopify_product_js_uses_minor_units_and_complete_gallery(self):
        product = collection_fast_client._shopify_product_from_json(
            {
                "title": "Oversize Mouse T-shirt - Acai",
                "price": 1990,
                "compare_at_price": 2490,
                "images": [
                    "//cdn.shopify.com/files/front.jpg?v=1",
                    "//cdn.shopify.com/files/back.jpg?v=2",
                ],
                "variants": [{
                    "available": True,
                    "price": 1990,
                    "compare_at_price": 2490,
                }],
            },
            "https://kleidermafia.com/products/oversize-mouse-t-shirt-acai",
        )

        self.assertEqual(str(product["price"]), "19.9")
        self.assertEqual(str(product["compare_at_price"]), "24.9")
        self.assertEqual(len(product["images"]), 2)
        self.assertEqual(
            product["images"][0],
            "https://cdn.shopify.com/files/front.jpg?v=1",
        )

    def test_builds_shopify_json_url_for_localized_product_path(self):
        self.assertEqual(
            collection_fast_client._shopify_product_json_url(
                "https://shop.example/de-de/products/hoodie?variant=1"
            ),
            "https://shop.example/de-de/products/hoodie.js",
        )

    def test_fetch_product_tries_shopify_json_before_product_html(self):
        response = MagicMock()
        response.is_redirect = False
        response.is_error = False
        response.json.return_value = {
            "title": "Shopify Hoodie",
            "variants": [{"available": True, "price": 3995}],
            "images": [
                "https://cdn.example/front.jpg",
                "https://cdn.example/back.jpg",
            ],
        }
        client = MagicMock()
        client.get.return_value = response
        product_url = "https://shop.example/products/shopify-hoodie"

        with patch.object(collection_fast_client, "_is_public_http_url", return_value=True):
            result = collection_fast_client._fetch_product(client, product_url)

        self.assertEqual(result["title"], "Shopify Hoodie")
        self.assertEqual(len(result["images"]), 2)
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(
            client.get.call_args.args[0],
            "https://shop.example/products/shopify-hoodie.js",
        )

    def test_fetch_product_can_skip_shopify_json_for_shopbase_gallery(self):
        product_url = "https://pumpbear.com/products/hydrated-hoodie"
        response = MagicMock()
        response.is_redirect = False
        response.is_error = False
        response.headers = {}
        response.url = product_url
        response.text = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "Hydrated Hoodie",
          "image": ["https://cdn.example/front.jpg"],
          "offers": {"price": "46.95", "priceCurrency": "USD"}
        }
        </script>
        <script>
        window.__STATE__={"product":{"handle":"hydrated-hoodie","id":123,
          "title":"Hydrated Hoodie","images":[
            {"src":"https://cdn.example/front.jpg?width=256"},
            {"src":"https://cdn.example/back.jpg?width=256"},
            {"src":"https://cdn.example/detail.jpg?width=256"}
          ],"variants":[{"available":true,"price":46.95}]}};
        </script>
        """
        client = MagicMock()
        client.get.return_value = response

        with patch.object(collection_fast_client, "_is_public_http_url", return_value=True):
            result = collection_fast_client._fetch_product(
                client,
                product_url,
                try_shopify_json=False,
            )

        self.assertEqual(len(result["images"]), 3)
        self.assertEqual(result["images"][2], "https://cdn.example/detail.jpg")
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(client.get.call_args.args[0], product_url)

    @patch.object(collection_fast_client.time, "sleep")
    @patch.object(collection_fast_client, "_fetch_product")
    def test_fast_path_retries_failed_urls_with_lower_concurrency(
        self, fetch_product, sleep
    ):
        product_url = "https://pumpbear.com/products/recovered-hoodie"
        product = {
            "title": "Recovered Hoodie",
            "price": 59.95,
            "currency": "USD",
            "compare_at_price": None,
            "product_url": product_url,
            "images": ["https://cdn.example/front.jpg"],
        }
        fetch_product.side_effect = [None, product]

        result = collection_fast_client.scrape_products_fast(
            [product_url],
            max_workers=1,
            try_shopify_json=False,
        )

        self.assertEqual(result["failed_urls"], [])
        self.assertEqual(result["products_by_url"][product_url], product)
        self.assertEqual(result["retry_rounds"], 1)
        self.assertEqual(result["retry_recovered_count"], 1)
        sleep.assert_called_once_with(2.0)

    @patch.object(collection_fast_client, "_fetch_product")
    def test_fast_path_reports_each_completed_product(self, fetch_product):
        urls = [
            f"https://shop.example/products/item-{index}"
            for index in range(3)
        ]
        fetch_product.side_effect = [
            {
                "title": f"Item {index}",
                "price": 10,
                "product_url": url,
                "images": [f"{url}.jpg"],
            }
            for index, url in enumerate(urls)
        ]
        events = []

        result = collection_fast_client.scrape_products_fast(
            urls,
            max_workers=2,
            recovery_delays=(),
            progress_callback=events.append,
        )

        self.assertEqual(len(result["products"]), 3)
        self.assertEqual([event["completed"] for event in events], [1, 2, 3])
        self.assertEqual(events[-1]["total"], 3)
        self.assertEqual(events[-1]["stage"], "fast_path")

    @patch.object(collection_fast_client, "scrape_products_fast")
    def test_shopbase_fast_path_uses_one_session_for_every_url(self, scrape):
        urls = [
            f"https://pumpbear.com/products/product-{index}"
            for index in range(45)
        ]
        def products_for(targets):
            return {
                url: {
                    "title": url.rsplit("/", 1)[-1],
                    "price": 10,
                    "product_url": url,
                    "images": [f"{url}.jpg"],
                }
                for url in targets
            }
        products_by_url = products_for(urls)
        scrape.return_value = {
            "products": list(products_by_url.values()),
            "products_by_url": products_by_url,
            "failed_urls": [],
            "retry_recovered_count": 0,
        }

        result = collection_fast_client.scrape_shopbase_products_fast(urls)

        scrape.assert_called_once_with(
            urls,
            max_workers=collection_fast_client.SHOPBASE_MAX_WORKERS,
            try_shopify_json=False,
            fetch_attempts=1,
            recovery_delays=collection_fast_client.SHOPBASE_RECOVERY_DELAYS_SEC,
            read_timeout=20,
        )
        self.assertEqual(result["attempted_count"], 45)
        self.assertEqual(len(result["products"]), 45)
        self.assertEqual(result["recovery_attempted"], 0)

    @patch.object(collection_fast_client, "scrape_products_fast")
    def test_shopbase_fast_path_retries_only_partial_failures(self, scrape):
        urls = [
            f"https://pumpbear.com/products/product-{index}"
            for index in range(4)
        ]
        products = {
            url: {
                "title": url.rsplit("/", 1)[-1],
                "price": 10,
                "product_url": url,
                "images": [f"{url}.jpg"],
            }
            for url in urls
        }
        scrape.return_value = {
            "products": list(products.values()),
            "products_by_url": products,
            "failed_urls": [],
            "retry_recovered_count": 2,
        }

        result = collection_fast_client.scrape_shopbase_products_fast(urls)

        scrape.assert_called_once()
        self.assertEqual(result["failed_urls"], [])
        self.assertEqual(result["recovery_attempted"], 2)

    @patch.object(collection_fast_client, "scrape_products_fast")
    def test_shopbase_fast_path_stops_after_fully_blocked_probe(self, scrape):
        urls = [
            f"https://pumpbear.com/products/product-{index}"
            for index in range(45)
        ]
        scrape.return_value = {
            "products": [],
            "products_by_url": {},
            "failed_urls": urls,
            "retry_recovered_count": 0,
        }

        result = collection_fast_client.scrape_shopbase_products_fast(urls)

        scrape.assert_called_once()
        self.assertEqual(result["attempted_count"], 45)
        self.assertTrue(result["stopped_by_waf"])
        self.assertEqual(result["failed_urls"], urls)

    @patch.object(collection_fast_client, "scrape_products_fast")
    def test_large_shopbase_fast_path_trips_circuit_breaker_after_probe(self, scrape):
        urls = [
            f"https://shop.example/products/product-{index}"
            for index in range(collection_fast_client.SHOPBASE_CIRCUIT_BREAKER_MIN_URLS)
        ]
        probe_urls = urls[:collection_fast_client.SHOPBASE_PROBE_SIZE]
        recovered_url = probe_urls[0]
        recovered_product = {
            "title": "Recovered",
            "price": 10,
            "product_url": recovered_url,
            "images": [f"{recovered_url}.jpg"],
        }
        scrape.return_value = {
            "products": [recovered_product],
            "products_by_url": {recovered_url: recovered_product},
            "failed_urls": probe_urls[1:],
            "retry_recovered_count": 0,
            "elapsed_seconds": 1,
        }

        result = collection_fast_client.scrape_shopbase_products_fast(urls)

        scrape.assert_called_once()
        self.assertEqual(scrape.call_args.args[0], probe_urls)
        self.assertEqual(scrape.call_args.kwargs["recovery_delays"], ())
        self.assertEqual(result["attempted_count"], len(probe_urls))
        self.assertTrue(result["circuit_breaker"])
        self.assertTrue(result["stopped_by_waf"])
        self.assertEqual(len(result["failed_urls"]), len(urls) - 1)

    def test_parses_product_json_ld_and_visible_compare_price(self):
        page_html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "Basketball Hoodie",
          "image": [
            {"url": "https://cdn.example/one.jpg?w=2048&h=2048"},
            "https://cdn.example/two.jpg?token=abc&width=900"
          ],
          "offers": [{"@type": "Offer", "price": "39.99", "priceCurrency": "USD"}]
        }
        </script>
        <div class="product-price__item product-price__origin">$80.00 USD</div>
        """

        product = collection_fast_client.parse_product_html(
            page_html,
            "https://shop.example/products/hoodie?variant=blue#gallery",
        )

        self.assertEqual(product["title"], "Basketball Hoodie")
        self.assertEqual(product["price"], "39.99")
        self.assertEqual(product["currency"], "USD")
        self.assertEqual(product["compare_at_price"], "$80.00 USD")
        self.assertEqual(product["product_url"], "https://shop.example/products/hoodie")
        self.assertEqual(
            product["images"],
            ["https://cdn.example/one.jpg", "https://cdn.example/two.jpg?token=abc"],
        )

    def test_pumpbear_json_ld_gallery_is_authoritative_for_fast_path(self):
        page_html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "Atlanta Falcons Touchdown Classic Varsity Jacket P2X5VS",
          "image": [
            "https://cdn.pumpbear.com/products/100/1-front.jpg?width=900",
            "https://cdn.pumpbear.com/products/100/2-back.jpg"
          ],
          "offers": {"price": "99.00", "priceCurrency": "USD"}
        }
        </script>
        """

        product = collection_fast_client.parse_product_html(
            page_html,
            "https://pumpbear.com/products/atlanta-falcons-touchdown-classic-varsity-jacket-p2x5vs",
        )

        self.assertTrue(product["_gallery_complete"])
        self.assertEqual(product["price"], "99.00")
        self.assertEqual(
            product["images"],
            [
                "https://cdn.pumpbear.com/products/100/1-front.jpg",
                "https://cdn.pumpbear.com/products/100/2-back.jpg",
            ],
        )

    def test_woocommerce_html_gallery_replaces_schema_lead_and_excludes_page_assets(self):
        page_html = """
        <header><img src="https://cdn.example/logo.png"></header>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "AJ1 Shoe",
          "image": ["https://cdn.example/schema-lead.jpg"],
          "offers": {"price": "78.95", "priceCurrency": "USD"}
        }
        </script>
        <div class="woocommerce-product-gallery images">
          <figure class="woocommerce-product-gallery__wrapper">
            <div class="woocommerce-product-gallery__image">
              <a href="https://cdn.example/front-full.jpg">
                <img src="https://cdn.example/front-thumb.jpg"
                     data-large_image="https://cdn.example/front-full.jpg">
              </a>
            </div>
            <div class="woocommerce-product-gallery__image">
              <a href="https://cdn.example/back-full.jpg">
                <img src="https://cdn.example/back-thumb.jpg"
                     data-large_image="https://cdn.example/back-full.jpg">
              </a>
            </div>
            <div class="woocommerce-product-gallery__image--placeholder">
              <a href="https://shop.example/wp-content/uploads/woocommerce-placeholder.webp">
                <img alt="Awaiting product image"
                     src="https://shop.example/wp-content/uploads/woocommerce-placeholder.webp">
              </a>
            </div>
          </figure>
        </div>
        <section class="related"><img src="https://cdn.example/related.jpg"></section>
        """

        product = collection_fast_client.parse_product_html(
            page_html,
            "https://shop.example/product/aj1-shoe/",
        )

        self.assertTrue(product["_gallery_complete"])
        self.assertEqual(
            product["images"],
            [
                "https://cdn.example/front-full.jpg",
                "https://cdn.example/back-full.jpg",
            ],
        )

    def test_woocommerce_placeholder_is_not_a_complete_gallery(self):
        page_html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "AJ1 Shoe Without Source Image",
          "image": ["https://shop.example/wp-content/uploads/woocommerce-placeholder.webp"],
          "offers": {"price": "78.95", "priceCurrency": "USD"}
        }
        </script>
        <div class="woocommerce-product-gallery woocommerce-product-gallery--without-images">
          <figure><a href="https://shop.example/wp-content/uploads/woocommerce-placeholder.webp">
            <img alt="Awaiting product image"
                 data-large_image="https://shop.example/wp-content/uploads/woocommerce-placeholder.webp">
          </a></figure>
        </div>
        """

        product = collection_fast_client.parse_product_html(
            page_html,
            "https://shop.example/product/no-image/",
        )

        self.assertIsNone(product)

    def test_sitebase_hydration_replaces_lead_image_with_full_gallery(self):
        page_html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "Hydrated Hoodie",
          "image": ["https://cdn.example/front.jpg"],
          "offers": {"price": "46.95", "priceCurrency": "USD"}
        }
        </script>
        <script>
        window.__STATE__={"product":{"handle":"hydrated-hoodie","id":123,
          "title":"Hydrated Hoodie","images":[
            {"src":"https://cdn.example/front.jpg?width=256"},
            {"src":"https://cdn.example/back.jpg?width=256"},
            {"src":"https://cdn.example/detail.jpg?width=256"}
          ],"variants":[{"available":true,"price":46.95,"compare_at_price":59.95}]}};
        </script>
        """

        product = collection_fast_client.parse_product_html(
            page_html,
            "https://shop.example/products/hydrated-hoodie",
        )

        self.assertEqual(product["title"], "Hydrated Hoodie")
        self.assertEqual(product["price"], 46.95)
        self.assertEqual(product["compare_at_price"], 59.95)
        self.assertEqual(product["currency"], "USD")
        self.assertEqual(
            product["images"],
            [
                "https://cdn.example/front.jpg",
                "https://cdn.example/back.jpg",
                "https://cdn.example/detail.jpg",
            ],
        )

    def test_sitebase_hydration_excludes_flags_theme_and_related_images(self):
        page_html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "Bluey Hoodie",
          "image": ["https://img.thesitebase.net/card.jpg"],
          "offers": {"price": "46.95", "priceCurrency": "USD"}
        }
        </script>
        <script>
        window.__STATE__={"product":{"handle":"bluey-hoodie","id":123,
          "title":"Bluey Hoodie","images":[
            {"src":"https://img.thesitebase.net/img/flags/usa.png?min_height=0"},
            {"src":"https://img.thesitebase.net/store/themes/logo.png"},
            {"src":"https://img.thesitebase.net/store/products/ver_1/front.jpeg"},
            {"src":"https://img.thesitebase.net/store/products/ver_1/back.jpeg"},
            {"src":"https://img.thesitebase.net/store/products/ver_1/front.jpeg?min_height=0"},
            {"src":"https://img.thesitebase.net/store/products/related.jpeg"},
            {"src":"https://img.thesitebase.net/files/trust-badge.png"}
          ],"variants":[{"available":true,"price":46.95}]}};
        </script>
        """

        product = collection_fast_client.parse_product_html(
            page_html,
            "https://pumpbear.com/products/bluey-hoodie",
        )

        self.assertEqual(
            product["images"],
            [
                "https://img.thesitebase.net/store/products/ver_1/front.jpeg",
                "https://img.thesitebase.net/store/products/ver_1/back.jpeg",
            ],
        )


class CollectionCsvTests(SimpleTestCase):
    def test_writes_one_product_per_row_with_dynamic_image_columns(self):
        content = collection_csv.build_collection_csv([
            {
                "title": "Chair, Walnut",
                "price": "49.5",
                "compare_at_price": "80",
                "currency": "USD",
                "product_url": "https://shop.example/products/chair",
                "images": ["https://cdn.example/1.jpg", "https://cdn.example/2.jpg"],
            },
            {
                "title": "Desk",
                "price": 100,
                "currency": "USD",
                "product_url": "https://shop.example/products/desk",
                "images": ["https://cdn.example/desk.jpg"],
            },
        ])

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        self.assertEqual(
            reader.fieldnames,
            [
                "title", "price", "compare_at_price", "product_url", "image_count",
                "image_1", "image_2", "image_3", "image_4", "image_5",
                "image_6", "image_7", "image_8", "image_9", "image_10",
            ],
        )
        self.assertEqual(rows[0]["title"], "Chair, Walnut")
        self.assertEqual(rows[0]["price"], "$49.50 USD")
        self.assertEqual(rows[0]["compare_at_price"], "$80.00 USD")
        self.assertEqual(rows[0]["product_url"], "https://shop.example/products/chair")
        self.assertEqual(rows[0]["image_count"], "2")
        self.assertEqual(rows[0]["image_2"], "https://cdn.example/2.jpg")
        self.assertEqual(rows[1]["price"], "$100.00 USD")
        self.assertEqual(rows[1]["image_2"], "")


class CollectionCsvViewTests(TestCase):
    def _job_token(self) -> str:
        return signing.dumps(
            {
                "job_id": "batch-123",
                "collection_url": "https://globeflags.com/collections/basketball-fan-hoodies",
                "requested": 2,
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )

    def _scan_token(self) -> str:
        return signing.dumps(
            {
                "collection_url": "https://globeflags.com/collections/basketball-fan-hoodies",
                "urls": ["https://globeflags.com/products/hoodie"],
                "scan": {"product_count": 1, "extract_credits": 5},
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )

    @patch.object(firecrawl_client, "start_collection_product_export")
    @patch.object(
        firecrawl_client,
        "scrape_sitebase_product_galleries_via_browser",
    )
    @patch.object(collection_fast_client, "scrape_shopbase_products_fast")
    def test_shopbase_reuses_recent_complete_gallery_without_refetching(
        self, fast_scrape, browser_recovery, firecrawl_start
    ):
        product_url = "https://pumpbear.com/products/cached-hoodie"
        cached_product = {
            "title": "Cached Hoodie",
            "price": 59.95,
            "currency": "USD",
            "product_url": product_url,
            "images": [
                "https://img.thesitebase.net/store/products/ver_1/front.jpg",
                "https://img.thesitebase.net/store/products/ver_1/back.jpg",
            ],
        }
        views.CollectionExportState.objects.create(
            state_id="cached-shopbase-gallery",
            collection_url="https://pumpbear.com/search?q=hoodie",
            product_urls=[product_url],
            fast_products_by_url={product_url: cached_product},
        )
        scan_token = signing.dumps(
            {
                "collection_url": "https://pumpbear.com/search?q=hoodie",
                "urls": [product_url],
                "scan": {
                    "product_count": 1,
                    "platform": "shopbase",
                    "discovery_mode": "search_api",
                    "waf_blocked": True,
                },
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )

        response = self.client.post(
            reverse("collection_csv"),
            {"action": "start", "scan_token": scan_token},
        )

        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(
            response.content.decode("utf-8-sig")
        )))
        self.assertEqual(rows[0]["title"], "Cached Hoodie")
        self.assertEqual(rows[0]["image_count"], "2")
        fast_scrape.assert_not_called()
        browser_recovery.assert_not_called()
        firecrawl_start.assert_not_called()

    def test_collection_template_compiles(self):
        self.assertIsNotNone(get_template("products/collection_csv.html"))
        self.assertIsNotNone(get_template("products/etsy_csv.html"))

    @patch("products.context_processors.pc.default_shop_id", return_value="")
    @patch("products.context_processors.pc.list_shops", return_value=[])
    def test_dedicated_etsy_screen_renders(self, _list_shops, _default_shop_id):
        response = self.client.get(reverse("etsy_csv"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Etsy shop/collection/search")
        self.assertContains(response, "section_id")
        self.assertContains(response, "search_query")

    @patch.object(firecrawl_client, "discover_etsy_listing_links")
    def test_etsy_screen_scans_with_marketplace_adapter(self, discover):
        discover.return_value = {
            "urls": [
                "https://www.etsy.com/listing/1/one",
                "https://www.etsy.com/listing/2/two",
            ],
            "pages_scanned": 1,
            "credits_used": 1,
            "truncated": False,
            "discovery_mode": "etsy_search",
        }
        source_url = "https://www.etsy.com/shop/MichaelOrn?search_query=mickey"

        response = self.client.post(
            reverse("etsy_csv"),
            {"action": "scan", "source_url": source_url},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<strong>2 listing</strong>", html=True)
        discover.assert_called_once_with(source_url)

    @patch("products.context_processors.pc.default_shop_id", return_value="")
    @patch("products.context_processors.pc.list_shops", return_value=[])
    def test_collection_screen_renders(self, _list_shops, _default_shop_id):
        response = self.client.get(reverse("collection_csv"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Collection/Search/Marketplace → CSV title, giá và ảnh")
        self.assertContains(response, "Quét Shopify")
        self.assertContains(response, "Quét ShopBase")
        self.assertContains(response, "Quét WooCommerce")
        self.assertContains(response, "formData.set('platform'")
        self.assertContains(response, reverse("collection_csv_scan_start"))

    @patch.object(views.threading, "Thread")
    def test_collection_scan_starts_background_job(self, thread_cls):
        response = self.client.post(
            reverse("collection_csv_scan_start"),
            {
                "collection_url": "https://shop.example/search?q=nfl",
                "platform": "shopbase",
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn(reverse("collection_csv_scan_progress"), payload["progress_url"])
        thread_cls.assert_called_once()
        self.assertTrue(thread_cls.call_args.kwargs["daemon"])
        thread_cls.return_value.start.assert_called_once_with()

    def test_collection_scan_progress_renders_real_page_counter(self):
        job_id = "scan-progress-test"
        token = signing.dumps(
            {
                "job_id": job_id,
                "state_id": f"scan-job-{job_id}",
                "collection_url": "https://shop.example/search?q=nfl",
                "platform": "shopbase",
            },
            salt=views.COLLECTION_SCAN_JOB_TOKEN_SALT,
            compress=True,
        )

        response = self.client.get(
            reverse("collection_csv_scan_progress"),
            {"token": token},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Đang tìm toàn bộ sản phẩm")
        self.assertContains(response, "pages_completed")
        self.assertContains(response, reverse("collection_csv_scan_status"))

    @patch.object(firecrawl_client, "start_collection_product_export")
    @patch.object(collection_fast_client, "scrape_products_fast")
    def test_full_gallery_updates_detail_crawl_progress(
        self, fast_scrape, firecrawl_start
    ):
        product_url = "https://globeflags.com/products/hoodie"
        fast_scrape.return_value = {
            "products": [{
                "title": "Hoodie",
                "price": 39.99,
                "product_url": product_url,
                "images": ["https://cdn.example/front.jpg"],
            }],
            "products_by_url": {product_url: {
                "title": "Hoodie",
                "price": 39.99,
                "product_url": product_url,
                "images": ["https://cdn.example/front.jpg"],
            }},
            "failed_urls": [],
        }
        progress_id = "detail-progress-test"
        progress_token = signing.dumps(
            {"progress_id": progress_id},
            salt=views.COLLECTION_DETAIL_PROGRESS_TOKEN_SALT,
            compress=True,
        )

        response = self.client.post(
            reverse("collection_csv"),
            {
                "action": "start",
                "scan_token": self._scan_token(),
                "image_mode": "full_gallery",
                "detail_progress_id": progress_id,
                "detail_progress_token": progress_token,
            },
        )
        status = self.client.get(
            reverse("collection_csv_detail_progress_status"),
            {"progress_id": progress_id, "token": progress_token},
        ).json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["processed"], 1)
        self.assertEqual(status["percent"], 100)
        self.assertIn("progress_callback", fast_scrape.call_args.kwargs)
        firecrawl_start.assert_not_called()

    def test_collection_card_scan_is_not_reused_as_full_gallery(self):
        product_url = "https://shop.example/products/card-only"
        views.CollectionExportState.objects.create(
            state_id="scan-job-card-only",
            collection_url="https://shop.example/search?q=nfl",
            product_urls=[product_url],
            fast_products_by_url={
                views.COLLECTION_SCAN_STATE_META_KEY: {
                    "scan_summary": {"collection_images_available": True}
                },
                product_url: {
                    "title": "Card only",
                    "price": 29.99,
                    "product_url": product_url,
                    "images": ["https://cdn.example/card.jpg"],
                },
            },
        )

        reusable = views._reusable_collection_products(
            [product_url],
            "https://shop.example/search?q=nfl",
        )

        self.assertEqual(reusable, {})

    @patch("products.context_processors.pc.default_shop_id", return_value="")
    @patch("products.context_processors.pc.list_shops", return_value=[])
    def test_progress_screen_renders_from_signed_job_token(self, _list_shops, _default_shop_id):
        response = self.client.get(
            reverse("collection_csv_progress"),
            {"token": self._job_token()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Đang hoàn thiện 2 sản phẩm")

    @patch.object(firecrawl_client, "start_collection_product_export")
    @patch.object(collection_fast_client, "scrape_products_fast")
    def test_start_uses_fast_path_and_returns_csv_without_firecrawl(self, fast_scrape, firecrawl_start):
        fast_scrape.return_value = {
            "products": [{
                "title": "Hoodie",
                "price": "39.99",
                "compare_at_price": "$80.00 USD",
                "currency": "USD",
                "product_url": "https://globeflags.com/products/hoodie",
                "images": ["https://cdn.example/hoodie.jpg"],
            }],
            "failed_urls": [],
            "elapsed_seconds": 1.2,
        }

        response = self.client.post(
            reverse("collection_csv"),
            {"action": "start", "scan_token": self._scan_token()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        firecrawl_start.assert_not_called()

    @patch.object(collection_fast_client, "scrape_products_fast")
    @patch.object(firecrawl_client, "start_collection_product_export")
    def test_start_falls_back_to_async_firecrawl_when_fast_path_is_incomplete(
        self, firecrawl_start, fast_scrape
    ):
        fast_scrape.return_value = {
            "products": [],
            "failed_urls": ["https://globeflags.com/products/hoodie"],
            "elapsed_seconds": 0.2,
        }
        firecrawl_start.return_value = {
            "job_id": "batch-123",
            "requested": 1,
            "invalid_urls": [],
        }

        response = self.client.post(
            reverse("collection_csv"),
            {"action": "start", "scan_token": self._scan_token()},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("collection_csv_progress"), response["Location"])
        firecrawl_start.assert_called_once_with(
            ["https://globeflags.com/products/hoodie"]
        )

    @patch.object(collection_fast_client, "scrape_products_fast")
    @patch.object(firecrawl_client, "start_collection_product_export")
    def test_start_ignores_catalogue_card_images_and_crawls_product_gallery(
        self, firecrawl_start, fast_scrape
    ):
        product_url = "https://shop.example/products/hoodie"
        token_with_card_image = signing.dumps(
            {
                "collection_url": "https://shop.example/search?q=hoodie",
                "urls": [product_url],
                "scan": {"product_count": 1, "discovery_mode": "search_api"},
                "products": [{
                    "title": "Listing Hoodie",
                    "price": 39.95,
                    "product_url": product_url,
                    "images": ["https://cdn.example/card-only.jpg"],
                }],
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )
        fast_scrape.return_value = {
            "products": [],
            "products_by_url": {},
            "failed_urls": [product_url],
            "elapsed_seconds": 0.2,
        }
        firecrawl_start.return_value = {
            "job_id": "batch-gallery",
            "requested": 1,
            "invalid_urls": [],
        }

        try:
            response = self.client.post(
                reverse("collection_csv"),
                {"action": "start", "scan_token": token_with_card_image},
            )
            self.assertEqual(response.status_code, 302)
            fast_scrape.assert_called_once_with([product_url])
            firecrawl_start.assert_called_once_with([product_url])
        finally:
            cache.delete(views._collection_export_cache_key("batch-gallery"))

    @patch.object(firecrawl_client, "start_collection_product_export")
    @patch.object(collection_fast_client, "scrape_shopbase_products_fast")
    def test_shopbase_full_gallery_opens_product_html_without_shopify_json(
        self, fast_scrape, firecrawl_start
    ):
        product_url = "https://pumpbear.com/products/hydrated-hoodie"
        scan_token = signing.dumps(
            {
                "collection_url": "https://pumpbear.com/collections/hoodies",
                "urls": [product_url],
                "scan": {
                    "product_count": 1,
                    "discovery_mode": "shopbase_collection_api",
                    "collection_images_available": True,
                    # Legacy ShopBase scan tokens incorrectly marked their
                    # one-to-two card images as a complete gallery. The start
                    # view must ignore that stale flag and open product HTML.
                    "full_gallery_available": True,
                },
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )
        fast_scrape.return_value = {
            "products": [{
                "title": "Hydrated Hoodie",
                "price": 46.95,
                "currency": "USD",
                "product_url": product_url,
                "images": [
                    "https://cdn.example/front.jpg",
                    "https://cdn.example/back.jpg",
                    "https://cdn.example/detail.jpg",
                ],
            }],
            "products_by_url": {},
            "failed_urls": [],
            "elapsed_seconds": 0.1,
        }

        response = self.client.post(
            reverse("collection_csv"),
            {
                "action": "start",
                "scan_token": scan_token,
                "image_mode": "full_gallery",
            },
        )

        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(
            response.content.decode("utf-8-sig")
        )))
        self.assertEqual(rows[0]["image_count"], "3")
        self.assertEqual(rows[0]["image_3"], "https://cdn.example/detail.jpg")
        fast_scrape.assert_called_once_with([product_url])
        firecrawl_start.assert_not_called()

    @patch.object(firecrawl_client, "start_collection_product_export")
    @patch.object(
        firecrawl_client,
        "scrape_sitebase_product_galleries_via_browser",
    )
    @patch.object(collection_fast_client, "scrape_shopbase_products_fast")
    def test_shopbase_rate_limited_fast_path_uses_grouped_browser_recovery(
        self, fast_scrape, browser_recovery, firecrawl_start
    ):
        product_url = "https://pumpbear.com/products/recovered-hoodie"
        scan_token = signing.dumps(
            {
                "collection_url": "https://pumpbear.com/search?q=hoodie",
                "urls": [product_url],
                "scan": {
                    "product_count": 1,
                    "platform": "shopbase",
                    "discovery_mode": "search_api",
                    "full_gallery_available": False,
                    "waf_blocked": True,
                },
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )
        fast_scrape.return_value = {
            "products": [],
            "products_by_url": {},
            "failed_urls": [product_url],
            "elapsed_seconds": 1.0,
            "retry_rounds": 3,
            "retry_recovered_count": 0,
        }
        recovered_product = {
            "title": "Recovered Hoodie",
            "price": 59.95,
            "currency": "USD",
            "compare_at_price": None,
            "product_url": product_url,
            "images": [
                "https://img.thesitebase.net/products/ver_1/front.jpg",
                "https://img.thesitebase.net/products/ver_1/back.jpg",
            ],
        }
        browser_recovery.return_value = {
            "products": [recovered_product],
            "products_by_url": {product_url: recovered_product},
            "failed_urls": [],
            "credits_used": 1,
            "batches": 1,
        }

        response = self.client.post(
            reverse("collection_csv"),
            {
                "action": "start",
                "scan_token": scan_token,
                "image_mode": "full_gallery",
            },
        )

        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(
            response.content.decode("utf-8-sig")
        )))
        self.assertEqual(rows[0]["image_count"], "2")
        fast_scrape.assert_called_once_with([product_url])
        browser_recovery.assert_called_once_with(
            "https://pumpbear.com/search?q=hoodie",
            [product_url],
        )
        firecrawl_start.assert_not_called()

    @patch.object(firecrawl_client, "start_collection_product_export")
    @patch.object(
        firecrawl_client,
        "scrape_sitebase_product_galleries_via_browser",
    )
    @patch.object(collection_fast_client, "scrape_shopbase_products_fast")
    def test_auto_detected_shopbase_insufficient_credits_does_not_start_per_product_batch(
        self, fast_scrape, browser_recovery, firecrawl_start
    ):
        product_url = "https://pumpbear.com/products/blocked-hoodie"
        scan_token = signing.dumps(
            {
                "collection_url": "https://pumpbear.com/search?q=hoodie",
                "urls": [product_url],
                "scan": {
                    "product_count": 1,
                    "discovery_mode": "search_api",
                    "waf_blocked": True,
                },
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )
        fast_scrape.return_value = {
            "products": [],
            "products_by_url": {},
            "failed_urls": [product_url],
        }
        browser_recovery.side_effect = RuntimeError(
            "Firecrawl không đủ credit: Insufficient credits"
        )

        response = self.client.post(
            reverse("collection_csv"),
            {"action": "start", "scan_token": scan_token},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Firecrawl không còn đủ credit")
        fast_scrape.assert_called_once_with([product_url])
        firecrawl_start.assert_not_called()

    @patch.object(firecrawl_client, "start_collection_product_export")
    @patch.object(collection_fast_client, "scrape_products_fast")
    def test_collection_image_mode_exports_cached_cards_without_product_crawl(
        self, fast_scrape, firecrawl_start
    ):
        product_url = "https://printerval.com/beer-sweater-p100"
        card_product = {
            "title": "Beer Sweater",
            "price": 26.95,
            "compare_at_price": 39.92,
            "currency": "USD",
            "product_url": product_url,
            "images": [
                "https://cdn.example/front.jpg",
                "https://cdn.example/back.jpg",
                "https://cdn.example/ignored-third.jpg",
            ],
        }
        state_id = "scan-printerval-cards"
        state_key = views._collection_scan_cache_key(state_id)
        state = {
            "collection_url": "https://printerval.com/search?q=beer+sweater",
            "product_urls": [product_url],
            "products": [card_product],
        }
        cache.set(state_key, state, views.COLLECTION_TOKEN_MAX_AGE)
        session = self.client.session
        session[state_key] = state
        session.save()
        scan_token = signing.dumps(
            {
                "collection_url": state["collection_url"],
                "urls": [product_url],
                "scan_state_id": state_id,
                "scan": {
                    "product_count": 1,
                    "collection_images_available": True,
                },
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )

        try:
            response = self.client.post(
                reverse("collection_csv"),
                {
                    "action": "start",
                    "scan_token": scan_token,
                    "image_mode": "collection_images",
                },
            )

            self.assertEqual(response.status_code, 200)
            rows = list(csv.DictReader(io.StringIO(
                response.content.decode("utf-8-sig")
            )))
            self.assertEqual(rows[0]["title"], "Beer Sweater")
            self.assertEqual(rows[0]["image_count"], "2")
            self.assertEqual(rows[0]["image_2"], "https://cdn.example/back.jpg")
            fast_scrape.assert_not_called()
            firecrawl_start.assert_not_called()
        finally:
            cache.delete(state_key)

    @patch.object(firecrawl_client, "start_collection_product_export")
    @patch.object(collection_fast_client, "scrape_products_fast")
    def test_shopify_full_gallery_mode_exports_scan_without_product_crawl(
        self, fast_scrape, firecrawl_start
    ):
        product_url = "https://shop.example/products/hoodie"
        state_id = "scan-shopify-full-gallery"
        state_key = views._collection_scan_cache_key(state_id)
        state = {
            "collection_url": "https://shop.example/collections/hoodies",
            "product_urls": [product_url],
            "products": [{
                "title": "Shopify Hoodie",
                "price": 39.95,
                "currency": "USD",
                "product_url": product_url,
                "images": [
                    "https://cdn.example/front.jpg",
                    "https://cdn.example/back.jpg",
                    "https://cdn.example/detail.jpg",
                ],
            }],
        }
        cache.set(state_key, state, views.COLLECTION_TOKEN_MAX_AGE)
        session = self.client.session
        session[state_key] = state
        session.save()
        scan_token = signing.dumps(
            {
                "collection_url": state["collection_url"],
                "urls": [product_url],
                "scan_state_id": state_id,
                "scan": {
                    "product_count": 1,
                    "collection_images_available": True,
                    "full_gallery_available": True,
                },
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )

        try:
            response = self.client.post(
                reverse("collection_csv"),
                {
                    "action": "start",
                    "scan_token": scan_token,
                    "image_mode": "full_gallery",
                },
            )

            self.assertEqual(response.status_code, 200)
            rows = list(csv.DictReader(io.StringIO(
                response.content.decode("utf-8-sig")
            )))
            self.assertEqual(rows[0]["image_count"], "3")
            self.assertEqual(rows[0]["image_3"], "https://cdn.example/detail.jpg")
            fast_scrape.assert_not_called()
            firecrawl_start.assert_not_called()
        finally:
            cache.delete(state_key)

    @patch.object(firecrawl_client, "discover_collection_product_links")
    def test_scan_preserves_shopify_full_gallery_for_download(self, discover):
        product_url = "https://shop.example/products/hoodie"
        discover.return_value = {
            "urls": [product_url],
            "pages_scanned": 1,
            "credits_used": 1,
            "truncated": False,
            "discovery_mode": "shopify_collection_api",
            "advertised_count": 1,
            "full_gallery_available": True,
            "products": [{
                "title": "Shopify Hoodie",
                "price": 39.95,
                "currency": "USD",
                "product_url": product_url,
                "images": [
                    "https://cdn.example/front.jpg",
                    "https://cdn.example/back.jpg",
                    "https://cdn.example/detail.jpg",
                ],
            }],
        }

        response = self.client.post(
            reverse("collection_csv"),
            {
                "action": "scan",
                "collection_url": "https://shop.example/collections/hoodies",
                "platform": "shopify",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = signing.loads(
            response.context["scan_token"], salt=views.COLLECTION_TOKEN_SALT
        )
        discover.assert_called_once_with(
            "https://shop.example/collections/hoodies",
            platform="shopify",
        )
        self.assertEqual(payload["scan"]["platform"], "shopify")
        self.assertTrue(payload["scan"]["full_gallery_available"])
        state_key = views._collection_scan_cache_key(payload["scan_state_id"])
        try:
            self.assertEqual(len(cache.get(state_key)["products"][0]["images"]), 3)
        finally:
            cache.delete(state_key)

    @patch.object(firecrawl_client, "discover_collection_product_links")
    def test_scan_routes_selected_shopbase_platform(self, discover):
        product_url = "https://pumpbear.com/products/hoodie"
        discover.return_value = {
            "urls": [product_url],
            "pages_scanned": 1,
            "credits_used": 0,
            "truncated": False,
            "discovery_mode": "shopbase_collection_api",
            "advertised_count": 1,
            "full_gallery_available": False,
            "products": [{
                "title": "ShopBase Hoodie",
                "price": 39.95,
                "currency": "USD",
                "product_url": product_url,
                "images": ["https://cdn.example/card.jpg"],
            }],
        }

        response = self.client.post(
            reverse("collection_csv"),
            {
                "action": "scan",
                "collection_url": "https://pumpbear.com/collections/hoodies",
                "platform": "shopbase",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "fetch(startForm.getAttribute('action')",
        )
        self.assertNotContains(response, "fetch(startForm.action")
        discover.assert_called_once_with(
            "https://pumpbear.com/collections/hoodies",
            platform="shopbase",
        )
        payload = signing.loads(
            response.context["scan_token"], salt=views.COLLECTION_TOKEN_SALT
        )
        self.assertEqual(payload["scan"]["platform"], "shopbase")
        self.assertEqual(payload["urls"], [])
        state_key = views._collection_scan_cache_key(payload["scan_state_id"])
        try:
            self.assertEqual(cache.get(state_key)["products"][0]["title"], "ShopBase Hoodie")
            download = self.client.post(
                reverse("collection_csv"),
                {
                    "action": "start",
                    "scan_token": response.context["scan_token"],
                    "image_mode": "collection_images",
                },
            )
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download["Content-Type"], "text/csv; charset=utf-8")
        finally:
            cache.delete(state_key)

    @patch.object(firecrawl_client, "discover_collection_product_links")
    def test_woocommerce_full_gallery_exports_store_api_images_without_crawl(
        self, discover
    ):
        product_url = "https://nailedbygeo.com/product/af1-shoe/"
        discover.return_value = {
            "urls": [product_url],
            "pages_scanned": 1,
            "credits_used": 0,
            "truncated": False,
            "discovery_mode": "woocommerce_store_api",
            "advertised_count": 1,
            "full_gallery_available": True,
            "products": [{
                "title": "AF1 Shoe",
                "price": 78.95,
                "currency": "USD",
                "product_url": product_url,
                "images": [
                    "https://cdn.example/front.jpg",
                    "https://cdn.example/back.jpg",
                    "https://cdn.example/detail.jpg",
                ],
            }],
        }

        scan_response = self.client.post(
            reverse("collection_csv"),
            {
                "action": "scan",
                "collection_url": "https://nailedbygeo.com/shop/?filter_category=af1",
                "platform": "woocommerce",
            },
        )

        self.assertEqual(scan_response.status_code, 200)
        scan_token = scan_response.context["scan_token"]
        payload = signing.loads(scan_token, salt=views.COLLECTION_TOKEN_SALT)
        self.assertTrue(payload["scan"]["full_gallery_available"])
        state_key = views._collection_scan_cache_key(payload["scan_state_id"])
        try:
            csv_response = self.client.post(
                reverse("collection_csv"),
                {
                    "action": "start",
                    "scan_token": scan_token,
                    "image_mode": "full_gallery",
                },
            )
            self.assertEqual(csv_response.status_code, 200)
            rows = list(csv.DictReader(io.StringIO(
                csv_response.content.decode("utf-8-sig")
            )))
            self.assertEqual(rows[0]["image_count"], "3")
            self.assertEqual(rows[0]["image_3"], "https://cdn.example/detail.jpg")
        finally:
            cache.delete(state_key)

    @patch.object(firecrawl_client, "start_collection_product_export")
    @patch.object(collection_fast_client, "scrape_products_fast")
    @patch.object(firecrawl_client, "discover_collection_product_links")
    def test_woocommerce_skips_empty_image_rows_without_product_crawl(
        self, discover, fast_scrape, firecrawl_start
    ):
        complete_url = "https://nailedbygeo.com/product/aj1-complete/"
        discover.return_value = {
            "urls": [complete_url],
            "pages_scanned": 1,
            "credits_used": 0,
            "truncated": False,
            "discovery_mode": "woocommerce_store_api",
            "advertised_count": 2,
            "fallback_product_count": 0,
            "skipped_no_image_count": 1,
            "full_gallery_available": True,
            "products": [
                {
                    "title": "AJ1 Complete",
                    "price": 78.95,
                    "currency": "USD",
                    "product_url": complete_url,
                    "images": [
                        "https://cdn.example/complete-front.jpg",
                        "https://cdn.example/complete-back.jpg",
                    ],
                },
            ],
        }

        scan_response = self.client.post(
            reverse("collection_csv"),
            {
                "action": "scan",
                "collection_url": (
                    "https://nailedbygeo.com/shop/?filter_category=aj1"
                ),
                "platform": "woocommerce",
            },
        )
        self.assertEqual(scan_response.status_code, 200)
        scan_token = scan_response.context["scan_token"]
        payload = signing.loads(scan_token, salt=views.COLLECTION_TOKEN_SALT)
        state_key = views._collection_scan_cache_key(payload["scan_state_id"])

        try:
            self.assertTrue(payload["scan"]["full_gallery_available"])
            self.assertEqual(payload["scan"]["extract_credits"], 0)
            self.assertEqual(payload["scan"]["fallback_product_count"], 0)
            self.assertEqual(payload["scan"]["skipped_no_image_count"], 1)
            self.assertEqual(payload["urls"], [])
            self.assertEqual(len(cache.get(state_key)["products"]), 1)

            csv_response = self.client.post(
                reverse("collection_csv"),
                {
                    "action": "start",
                    "scan_token": scan_token,
                    "image_mode": "full_gallery",
                },
            )

            self.assertEqual(csv_response.status_code, 200)
            rows = list(csv.DictReader(io.StringIO(
                csv_response.content.decode("utf-8-sig")
            )))
            self.assertEqual([row["title"] for row in rows], ["AJ1 Complete"])
            self.assertEqual(rows[0]["image_count"], "2")
            fast_scrape.assert_not_called()
            firecrawl_start.assert_not_called()
        finally:
            cache.delete(state_key)

    @patch.object(firecrawl_client, "discover_collection_product_links")
    def test_scan_stores_printerval_collection_cards_for_image_mode(self, discover):
        product_url = "https://printerval.com/beer-sweater-p100"
        discover.return_value = {
            "urls": [product_url],
            "pages_scanned": 1,
            "credits_used": 1,
            "truncated": False,
            "discovery_mode": "printerval_search",
            "advertised_count": 1,
            "products": [{
                "title": "Beer Sweater",
                "price": 26.95,
                "currency": "USD",
                "product_url": product_url,
                "images": ["https://cdn.example/card.jpg"],
            }],
        }

        response = self.client.post(
            reverse("collection_csv"),
            {
                "action": "scan",
                "collection_url": "https://printerval.com/search?q=beer+sweater",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ảnh trên collection — nhanh nhất")
        scan_token = response.context["scan_token"]
        payload = signing.loads(scan_token, salt=views.COLLECTION_TOKEN_SALT)
        self.assertTrue(payload["scan"]["collection_images_available"])
        state_key = views._collection_scan_cache_key(payload["scan_state_id"])
        try:
            self.assertEqual(cache.get(state_key)["products"][0]["title"], "Beer Sweater")
            self.assertIn(state_key, self.client.session)
        finally:
            cache.delete(state_key)

    def test_shopbase_collection_mode_keeps_image_less_catalogue_rows(self):
        urls = [
            "https://shop.example/products/with-image",
            "https://shop.example/products/resend-fee",
        ]
        products = [
            {
                "title": "With image",
                "price": 29.99,
                "product_url": urls[0],
                "images": ["https://cdn.example/card.jpg"],
            },
            {
                "title": "Resend fee",
                "price": 5,
                "product_url": urls[1],
                "images": [],
            },
        ]
        scan = {
            "urls": urls,
            "products": products,
            "pages_scanned": 1,
            "credits_used": 0,
            "discovery_mode": "search_api",
            "waf_blocked": False,
            "platform": "shopbase",
            "card_diagnostics": {
                "returned": 2,
                "with_title": 2,
                "with_price": 2,
                "with_images": 1,
            },
        }

        summary = views._build_collection_scan_summary(scan, "shopbase")
        rows, missing = views._collection_card_rows(
            urls,
            products,
            allow_empty_images=True,
        )

        self.assertTrue(summary["collection_images_available"])
        self.assertEqual(summary["card_no_image_count"], 1)
        self.assertEqual(missing, [])
        self.assertEqual(rows[1]["images"], [])

    @patch.object(collection_fast_client, "scrape_products_fast")
    @patch.object(firecrawl_client, "start_collection_product_export")
    def test_known_waf_store_skips_slow_direct_fast_path(
        self, firecrawl_start, fast_scrape
    ):
        product_url = "https://www.legendslockerssport.com/products/hoodie"
        scan_token = signing.dumps(
            {
                "collection_url": "https://www.legendslockerssport.com/search?q=hoodie",
                "urls": [product_url],
                "scan": {
                    "product_count": 1,
                    "discovery_mode": "search_api",
                    "waf_blocked": True,
                },
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )
        firecrawl_start.return_value = {
            "job_id": "batch-waf",
            "requested": 1,
            "invalid_urls": [],
        }

        try:
            response = self.client.post(
                reverse("collection_csv"),
                {"action": "start", "scan_token": scan_token},
            )
            self.assertEqual(response.status_code, 302)
            fast_scrape.assert_not_called()
            firecrawl_start.assert_called_once_with([product_url])
        finally:
            cache.delete(views._collection_export_cache_key("batch-waf"))

    @patch.object(collection_fast_client, "scrape_products_fast")
    @patch.object(firecrawl_client, "start_collection_product_export")
    def test_start_only_sends_failed_urls_and_caches_fast_rows(
        self, firecrawl_start, fast_scrape
    ):
        first_url = "https://globeflags.com/products/fast-hoodie"
        second_url = "https://globeflags.com/products/fallback-hoodie"
        fast_product = {
            "title": "Fast Hoodie",
            "price": "39.99",
            "currency": "USD",
            "product_url": first_url,
            "images": ["https://cdn.example/fast.jpg"],
        }
        scan_token = signing.dumps(
            {
                "collection_url": "https://globeflags.com/collections/hoodies",
                "urls": [first_url, second_url],
                "scan": {"product_count": 2, "extract_credits": 10},
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )
        fast_scrape.return_value = {
            "products": [fast_product],
            "products_by_url": {first_url: fast_product},
            "failed_urls": [second_url],
            "elapsed_seconds": 0.5,
        }
        firecrawl_start.return_value = {
            "job_id": "batch-partial",
            "requested": 1,
            "invalid_urls": [],
        }

        try:
            response = self.client.post(
                reverse("collection_csv"),
                {"action": "start", "scan_token": scan_token},
            )

            self.assertEqual(response.status_code, 302)
            firecrawl_start.assert_called_once_with([second_url])
            token = parse_qs(urlparse(response["Location"]).query)["token"][0]
            payload = signing.loads(token, salt=views.COLLECTION_TOKEN_SALT)
            self.assertEqual(payload["requested"], 2)
            self.assertEqual(payload["fast_count"], 1)
            self.assertEqual(payload["fallback_requested"], 1)
            state = cache.get(
                views._collection_export_cache_key(payload["state_id"])
            )
            self.assertEqual(state["product_urls"], [first_url, second_url])
            self.assertEqual(state["fast_products_by_url"][first_url], fast_product)
            session_state = self.client.session[
                views._collection_export_cache_key(payload["state_id"])
            ]
            self.assertEqual(session_state["product_urls"], [first_url, second_url])
        finally:
            cache.delete(views._collection_export_cache_key("batch-partial"))

    @patch.object(firecrawl_client, "get_collection_product_export")
    def test_early_download_returns_to_progress_screen(self, get_export):
        get_export.side_effect = firecrawl_client.CollectionBatchIncomplete(
            "Collection batch is not complete (74/412 pages)."
        )

        response = self.client.get(
            reverse("collection_csv_download"),
            {"token": self._job_token()},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("collection_csv_progress"), response["Location"])

    @patch.object(firecrawl_client, "get_collection_product_export")
    def test_download_recovers_all_firecrawl_job_after_cache_is_lost(self, get_export):
        products = [
            {
                "title": f"Hoodie {index}",
                "price": 49,
                "currency": "USD",
                "product_url": f"https://shop.example/products/hoodie-{index}",
                "images": [f"https://cdn.example/hoodie-{index}.jpg"],
            }
            for index in range(2)
        ]
        get_export.return_value = {"products": products, "credits_used": 2}
        state_id = "batch-cache-lost"
        cache.delete(views._collection_export_cache_key(state_id))
        token = signing.dumps(
            {
                "job_id": state_id,
                "state_id": state_id,
                "collection_url": "https://shop.example/search?q=hoodie",
                "requested": 2,
                "fast_count": 0,
                "fallback_requested": 2,
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )

        response = self.client.get(
            reverse("collection_csv_download"),
            {"token": token},
        )

        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["title"] for row in rows}, {"Hoodie 0", "Hoodie 1"})

    @patch.object(firecrawl_client, "get_collection_product_export")
    def test_download_returns_only_compact_csv_columns(self, get_export):
        get_export.return_value = {
            "products": [{
                "title": "Chair",
                "price": 49,
                "currency": "USD",
                "product_url": "https://shop.example/products/chair",
                "images": ["https://cdn.example/chair.jpg"],
            }],
            "credits_used": 5,
        }

        response = self.client.get(
            reverse("collection_csv_download"),
            {"token": self._job_token()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("globeflags_basketball_fan_hoodies.csv", response["Content-Disposition"])
        content = response.content.decode("utf-8-sig")
        self.assertEqual(
            content.splitlines()[0],
            "title,price,compare_at_price,product_url,image_count,image_1,image_2,image_3,image_4,image_5,image_6,image_7,image_8,image_9,image_10",
        )
        self.assertIn(
            "Chair,$49.00 USD,,https://shop.example/products/chair,1,https://cdn.example/chair.jpg",
            content,
        )

    @patch.object(firecrawl_client, "get_collection_product_export")
    def test_download_merges_fast_and_fallback_rows_in_collection_order(self, get_export):
        first_url = "https://shop.example/products/fast"
        second_url = "https://shop.example/products/fallback"
        fast_product = {
            "title": "Fast Product",
            "price": 10,
            "currency": "USD",
            "product_url": first_url,
            "images": ["https://cdn.example/fast.jpg"],
        }
        fallback_product = {
            "title": "Fallback Product",
            "price": 20,
            "currency": "USD",
            "product_url": second_url,
            "images": ["https://cdn.example/fallback.jpg"],
        }
        get_export.return_value = {
            "products": [fallback_product],
            "credits_used": 5,
        }
        state_id = "batch-merge"
        cache.set(
            views._collection_export_cache_key(state_id),
            {
                "product_urls": [first_url, second_url],
                "fast_products_by_url": {first_url: fast_product},
            },
            60,
        )
        token = signing.dumps(
            {
                "job_id": state_id,
                "state_id": state_id,
                "collection_url": "https://shop.example/collections/all",
                "requested": 2,
                "fast_count": 1,
                "fallback_requested": 1,
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )

        try:
            response = self.client.get(
                reverse("collection_csv_download"),
                {"token": token},
            )
            rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                [row["title"] for row in rows],
                ["Fast Product", "Fallback Product"],
            )
        finally:
            cache.delete(views._collection_export_cache_key(state_id))

    @patch.object(firecrawl_client, "get_collection_product_export")
    def test_download_recovers_mixed_export_state_from_database(self, get_export):
        first_url = "https://shop.example/products/fast"
        second_url = "https://shop.example/products/fallback"
        fast_product = {
            "title": "Fast Product",
            "price": 10,
            "currency": "USD",
            "product_url": first_url,
            "images": ["https://cdn.example/fast.jpg"],
        }
        fallback_product = {
            "title": "Fallback Product",
            "price": 20,
            "currency": "USD",
            "product_url": second_url,
            "images": ["https://cdn.example/fallback.jpg"],
        }
        get_export.return_value = {"products": [fallback_product], "credits_used": 1}
        state_id = "batch-database-recovery"
        views.CollectionExportState.objects.create(
            state_id=state_id,
            collection_url="https://shop.example/collections/all",
            product_urls=[first_url, second_url],
            fast_products_by_url={first_url: fast_product},
        )
        state_key = views._collection_export_cache_key(state_id)
        cache.delete(state_key)
        session = self.client.session
        session.pop(state_key, None)
        session.save()
        token = signing.dumps(
            {
                "job_id": state_id,
                "state_id": state_id,
                "collection_url": "https://shop.example/collections/all",
                "requested": 2,
                "fast_count": 1,
                "fallback_requested": 1,
            },
            salt=views.COLLECTION_TOKEN_SALT,
            compress=True,
        )

        response = self.client.get(
            reverse("collection_csv_download"),
            {"token": token},
        )

        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(
            [row["title"] for row in rows],
            ["Fast Product", "Fallback Product"],
        )
