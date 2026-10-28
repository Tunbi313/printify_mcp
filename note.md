{% liquid
  assign current_team = product.metafields.custom.team_name.value | strip | downcase
  assign products_shown = 0
  assign rendered_product_ids = '|'
  assign team_slider_id = 'custom-team-recommendations-' | append: section.id

  capture current_collection_ids
    for current_collection in product.collections limit: 20
      echo '|'
      echo current_collection.id
      echo '|'
    endfor
  endcapture
%}

{% if current_team != blank %}
  {% capture related_product_cards %}
    {% for source_collection in collections limit: 50 %}
      {% if products_shown >= 10 %}
        {% break %}
      {% endif %}

      {% capture source_collection_key %}|{{ source_collection.id }}|{% endcapture %}

      {% unless current_collection_ids contains source_collection_key %}
        {% for related_product in source_collection.products limit: 50 %}
          {% if products_shown >= 10 %}
            {% break %}
          {% endif %}

          {% capture related_product_key %}|{{ related_product.id }}|{% endcapture %}

          {% if related_product.id != product.id %}
            {% unless rendered_product_ids contains related_product_key %}
              {% assign related_team = related_product.metafields.custom.team_name.value | strip | downcase %}

              {% if related_team == current_team %}
                {% assign shares_collection = false %}

                {% for related_collection in related_product.collections limit: 20 %}
                  {% capture related_collection_key %}|{{ related_collection.id }}|{% endcapture %}

                  {% if current_collection_ids contains related_collection_key %}
                    {% assign shares_collection = true %}
                    {% break %}
                  {% endif %}
                {% endfor %}

                {% unless shares_collection %}
                  <div class="m:column custom-team-slider__slide" role="listitem">
                    {% liquid
                      case settings.pcard_layout
                        when '2'
                          render 'product-card-2', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                        when '3'
                          render 'product-card-3', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                        when '4'
                          render 'product-card-4', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                        when '5'
                          render 'product-card-5', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                        else
                          render 'product-card-1', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                      endcase
                    %}
                  </div>

                  {% assign rendered_product_ids = rendered_product_ids | append: related_product_key %}
                  {% assign products_shown = products_shown | plus: 1 %}
                {% endunless %}
              {% endif %}
            {% endunless %}
          {% endif %}
        {% endfor %}
      {% endunless %}
    {% endfor %}

    {% if products_shown < 10 %}
      {% for source_collection in collections limit: 50 %}
        {% if products_shown >= 10 %}
          {% break %}
        {% endif %}

        {% capture source_collection_key %}|{{ source_collection.id }}|{% endcapture %}

        {% unless current_collection_ids contains source_collection_key %}
          {% for related_product in source_collection.products limit: 50 %}
            {% if products_shown >= 10 %}
              {% break %}
            {% endif %}

            {% capture related_product_key %}|{{ related_product.id }}|{% endcapture %}

            {% if related_product.id != product.id %}
              {% unless rendered_product_ids contains related_product_key %}
                {% assign related_team = related_product.metafields.custom.team_name.value | strip | downcase %}

                {% if related_team == current_team %}
                  <div class="m:column custom-team-slider__slide" role="listitem">
                    {% liquid
                      case settings.pcard_layout
                        when '2'
                          render 'product-card-2', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                        when '3'
                          render 'product-card-3', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                        when '4'
                          render 'product-card-4', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                        when '5'
                          render 'product-card-5', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                        else
                          render 'product-card-1', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                      endcase
                    %}
                  </div>

                  {% assign rendered_product_ids = rendered_product_ids | append: related_product_key %}
                  {% assign products_shown = products_shown | plus: 1 %}
                {% endif %}
              {% endunless %}
            {% endif %}
          {% endfor %}
        {% endunless %}
      {% endfor %}
    {% endif %}

    {% if products_shown < 10 %}
      {% for source_collection in product.collections limit: 20 %}
        {% if products_shown >= 10 %}
          {% break %}
        {% endif %}

        {% for related_product in source_collection.products limit: 50 %}
          {% if products_shown >= 10 %}
            {% break %}
          {% endif %}

          {% capture related_product_key %}|{{ related_product.id }}|{% endcapture %}

          {% if related_product.id != product.id %}
            {% unless rendered_product_ids contains related_product_key %}
              {% assign related_team = related_product.metafields.custom.team_name.value | strip | downcase %}

              {% if related_team == current_team %}
                <div class="m:column custom-team-slider__slide" role="listitem">
                  {% liquid
                    case settings.pcard_layout
                      when '2'
                        render 'product-card-2', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                      when '3'
                        render 'product-card-3', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                      when '4'
                        render 'product-card-4', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                      when '5'
                        render 'product-card-5', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                      else
                        render 'product-card-1', product: related_product, index: products_shown, animated: false, animation_effect: 'none', remove_params: false
                    endcase
                  %}
                </div>

                {% assign rendered_product_ids = rendered_product_ids | append: related_product_key %}
                {% assign products_shown = products_shown | plus: 1 %}
              {% endif %}
            {% endunless %}
          {% endif %}
        {% endfor %}
      {% endfor %}
    {% endif %}
  {% endcapture %}

  {% if products_shown > 0 %}
    <section id="{{ team_slider_id }}" class="m-section m:block m-product-recommendations m-swiper-overflow">
      <div class="container m-section-my m-section-py">
        <div class="m-section__header m:text-left">
          <h2 class="m-section__heading h3">
            {{ 'products.product.product_recommendation_heading' | t }}
          </h2>
        </div>

        <div class="m-product-recommendations__wrapper m-slider-control-hover-inside m-mixed-layout custom-team-slider">
          <div class="m-product-list custom-team-slider__viewport" data-team-slider-viewport tabindex="0">
            <div class="m:grid m:grid-2-cols md:m:grid-3-cols xl:m:grid-4-cols m-mixed-layout__inner custom-team-slider__track" role="list">
              {{ related_product_cards }}
            </div>
          </div>

          <button class="custom-team-slider__button custom-team-slider__button--prev" type="button" aria-label="Previous products" data-team-slider-prev>
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M15 18 9 12l6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>

          <button class="custom-team-slider__button custom-team-slider__button--next" type="button" aria-label="Next products" data-team-slider-next>
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="m9 18 6-6-6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
        </div>
      </div>
    </section>

    <style>
      #{{ team_slider_id }} {
        --team-slider-columns: 4;
        --team-slider-gap: 30px;
        --section-padding-top: 50px;
        --section-padding-bottom: 50px;
      }

      #{{ team_slider_id }} .custom-team-slider {
        position: relative !important;
        overflow: visible !important;
      }

      #{{ team_slider_id }} .custom-team-slider__viewport {
        width: 100% !important;
        max-width: 100% !important;
        overflow-x: auto !important;
        overflow-y: hidden !important;
        scroll-behavior: smooth;
        scroll-snap-type: x mandatory;
        scrollbar-width: none;
        -ms-overflow-style: none;
      }

      #{{ team_slider_id }} .custom-team-slider__viewport::-webkit-scrollbar {
        display: none;
      }

      #{{ team_slider_id }} .custom-team-slider__track {
        box-sizing: border-box !important;
        display: flex !important;
        flex-wrap: nowrap !important;
        align-items: stretch !important;
        width: 100% !important;
        min-width: 100% !important;
        gap: var(--team-slider-gap) !important;
        margin: 0 !important;
      }

      #{{ team_slider_id }} .custom-team-slider__slide {
        box-sizing: border-box !important;
        flex: 0 0 calc((100% - (var(--team-slider-gap) * 3)) / var(--team-slider-columns)) !important;
        width: calc((100% - (var(--team-slider-gap) * 3)) / var(--team-slider-columns)) !important;
        min-width: 0 !important;
        max-width: none !important;
        scroll-snap-align: start;
      }

      #{{ team_slider_id }} .custom-team-slider__button {
        position: absolute !important;
        top: 42% !important;
        z-index: 50 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        width: 44px !important;
        height: 44px !important;
        padding: 0 !important;
        border: 1px solid rgba(0, 0, 0, .12) !important;
        border-radius: 50% !important;
        background: #fff !important;
        color: #111 !important;
        box-shadow: 0 3px 12px rgba(0, 0, 0, .12) !important;
        cursor: pointer !important;
        opacity: 0 !important;
        visibility: hidden !important;
        pointer-events: none !important;
        transform: translateY(-50%) !important;
        transition: opacity .2s ease, visibility .2s ease, background-color .2s ease, color .2s ease, transform .2s ease !important;
      }

      #{{ team_slider_id }}:hover .custom-team-slider__button,
      #{{ team_slider_id }}:focus-within .custom-team-slider__button {
        opacity: 1 !important;
        visibility: visible !important;
        pointer-events: auto !important;
      }

      #{{ team_slider_id }}:hover .custom-team-slider__button:disabled,
      #{{ team_slider_id }}:focus-within .custom-team-slider__button:disabled {
        opacity: .25 !important;
        pointer-events: none !important;
      }

      #{{ team_slider_id }} .custom-team-slider__button svg {
        width: 22px !important;
        height: 22px !important;
      }

      #{{ team_slider_id }} .custom-team-slider__button--prev {
        left: 8px !important;
      }

      #{{ team_slider_id }} .custom-team-slider__button--next {
        right: 8px !important;
      }

      #{{ team_slider_id }} .custom-team-slider__button:not(:disabled):hover {
        background: #111 !important;
        color: #fff !important;
        transform: translateY(-50%) scale(1.08) !important;
      }

      #{{ team_slider_id }} .custom-team-slider__button:disabled {
        opacity: 0 !important;
        cursor: default !important;
      }

      #{{ team_slider_id }} .custom-team-slider__button[hidden] {
        display: flex !important;
      }

      @media (max-width: 1023px) {
        #{{ team_slider_id }} {
          --team-slider-columns: 3;
          --team-slider-gap: 20px;
        }

        #{{ team_slider_id }} .custom-team-slider__slide {
          flex-basis: calc((100% - (var(--team-slider-gap) * 2)) / var(--team-slider-columns)) !important;
          width: calc((100% - (var(--team-slider-gap) * 2)) / var(--team-slider-columns)) !important;
        }
      }

      @media (max-width: 767px) {
        #{{ team_slider_id }} {
          --team-slider-gap: 16px;
        }

        #{{ team_slider_id }} .custom-team-slider__slide {
          flex: 0 0 70% !important;
          width: 70% !important;
        }

        #{{ team_slider_id }} .custom-team-slider__button,
        #{{ team_slider_id }} .custom-team-slider__button[hidden] {
          display: none !important;
        }
      }
    </style>

    <script>
      (function () {
        const root = document.getElementById({{ team_slider_id | json }});

        if (!root || root.dataset.teamSliderReady === 'true') return;

        const viewport = root.querySelector('[data-team-slider-viewport]');
        const previousButton = root.querySelector('[data-team-slider-prev]');
        const nextButton = root.querySelector('[data-team-slider-next]');

        if (!viewport || !previousButton || !nextButton) return;

        root.dataset.teamSliderReady = 'true';
        previousButton.hidden = false;
        nextButton.hidden = false;
        previousButton.removeAttribute('hidden');
        nextButton.removeAttribute('hidden');

        const updateControls = function () {
          const maximumScroll = viewport.scrollWidth - viewport.clientWidth;
          const hasOverflow = maximumScroll > 2;

          previousButton.disabled = !hasOverflow || viewport.scrollLeft <= 2;
          nextButton.disabled = !hasOverflow || viewport.scrollLeft >= maximumScroll - 2;
        };

        const moveSlider = function (direction) {
          viewport.scrollBy({
            left: direction * viewport.clientWidth,
            behavior: 'smooth'
          });
        };

        previousButton.addEventListener('click', function () {
          moveSlider(-1);
        });

        nextButton.addEventListener('click', function () {
          moveSlider(1);
        });

        viewport.addEventListener('scroll', updateControls, { passive: true });
        window.addEventListener('resize', updateControls);

        if ('ResizeObserver' in window) {
          new ResizeObserver(updateControls).observe(viewport);
        }

        requestAnimationFrame(function () {
          requestAnimationFrame(updateControls);
        });

        window.addEventListener('load', updateControls, { once: true });
      })();
    </script>
  {% endif %}
{% endif %}