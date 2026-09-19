--
-- PostgreSQL database dump
--

\restrict EKxqdlpNTAHGVZnCHATjLfMrflKh1zhRkprnWhqIUsAqWNYsb7s3emOs3gCDCW9

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: analytics_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analytics_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid,
    traveler_id uuid,
    destination_id uuid NOT NULL,
    event_name character varying(100) NOT NULL,
    entity_type character varying(50),
    entity_id uuid,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: attractions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attractions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    code character varying(20) NOT NULL,
    name character varying(200) NOT NULL,
    category character varying(100),
    zone character varying(100),
    description text,
    opening_hours text,
    adult_price numeric(12,2),
    child_price numeric(12,2),
    currency character(3),
    contact_or_email text,
    google_place_id character varying(255),
    google_maps_url text,
    latitude numeric(9,6),
    longitude numeric(9,6),
    verification_status character varying(50),
    confidence_score numeric(4,3),
    last_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: commissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commissions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(40) NOT NULL,
    reservation_id uuid NOT NULL,
    partner_id uuid NOT NULL,
    payment_id uuid,
    base_amount numeric(12,2) NOT NULL,
    commission_type character varying(20) NOT NULL,
    commission_rate numeric(10,2),
    commission_amount numeric(12,2) NOT NULL,
    currency character(3) DEFAULT 'PEN'::bpchar NOT NULL,
    trigger_event character varying(30) NOT NULL,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    earned_at timestamp with time zone,
    settled_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT commissions_base_amount_check CHECK ((base_amount >= (0)::numeric)),
    CONSTRAINT commissions_commission_amount_check CHECK ((commission_amount >= (0)::numeric)),
    CONSTRAINT commissions_commission_rate_check CHECK (((commission_rate IS NULL) OR (commission_rate >= (0)::numeric))),
    CONSTRAINT commissions_commission_type_check CHECK (((commission_type)::text = ANY ((ARRAY['percentage'::character varying, 'fixed'::character varying])::text[]))),
    CONSTRAINT commissions_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'earned'::character varying, 'settled'::character varying, 'cancelled'::character varying, 'disputed'::character varying])::text[]))),
    CONSTRAINT commissions_trigger_event_check CHECK (((trigger_event)::text = ANY ((ARRAY['payment_confirmed'::character varying, 'service_completed'::character varying, 'manual'::character varying])::text[])))
);


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    conversation_type character varying(50),
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: data_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_sources (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(200) NOT NULL,
    source_type character varying(50),
    url text,
    authority_level character varying(50),
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: destinations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.destinations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(20) NOT NULL,
    name character varying(100) NOT NULL,
    slug character varying(120) NOT NULL,
    country_code character(2) DEFAULT 'PE'::bpchar NOT NULL,
    region character varying(100),
    province character varying(100),
    district character varying(100),
    timezone character varying(50) DEFAULT 'America/Lima'::character varying NOT NULL,
    currency character(3) DEFAULT 'PEN'::bpchar NOT NULL,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: emergency_services; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.emergency_services (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    code character varying(20) NOT NULL,
    name character varying(200) NOT NULL,
    service_type character varying(100),
    address text,
    phone character varying(50),
    open_24h boolean,
    google_place_id character varying(255),
    google_maps_url text,
    zone character varying(100),
    latitude numeric(9,6),
    longitude numeric(9,6),
    verification_status character varying(50),
    confidence_score numeric(4,3),
    last_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: entity_embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_embeddings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    entity_type character varying(50) NOT NULL,
    entity_id uuid NOT NULL,
    content text NOT NULL,
    embedding public.vector(384),
    embedding_model character varying(100),
    content_hash character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: entity_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_sources (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_id uuid NOT NULL,
    entity_type character varying(50) NOT NULL,
    entity_id uuid NOT NULL,
    source_url text,
    verification_status character varying(50),
    confidence_score numeric(4,3),
    verified_at timestamp with time zone,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    venue_id uuid,
    code character varying(20),
    name character varying(200) NOT NULL,
    event_type character varying(100),
    description text,
    start_at timestamp with time zone,
    end_at timestamp with time zone,
    price numeric(12,2),
    currency character(3),
    booking_url text,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: general_services; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.general_services (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    code character varying(20) NOT NULL,
    name character varying(200) NOT NULL,
    category character varying(100),
    address text,
    phone character varying(50),
    opening_hours text,
    google_place_id character varying(255),
    google_maps_url text,
    zone character varying(100),
    latitude numeric(9,6),
    longitude numeric(9,6),
    verification_status character varying(50),
    confidence_score numeric(4,3),
    last_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hotels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hotels (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    code character varying(20) NOT NULL,
    name character varying(200) NOT NULL,
    category character varying(100),
    address text,
    phone character varying(50),
    email character varying(200),
    whatsapp character varying(50),
    website text,
    price_observed numeric(12,2),
    currency character(3),
    rating numeric(3,2),
    reviews_count integer,
    check_in time without time zone,
    check_out time without time zone,
    wifi boolean,
    parking boolean,
    pool boolean,
    spa boolean,
    gym boolean,
    beach_access boolean,
    ocean_view boolean,
    family_friendly boolean,
    pet_friendly boolean,
    has_restaurant boolean,
    has_bar boolean,
    google_place_id character varying(255),
    google_maps_url text,
    zone character varying(100),
    distance_to_reference_m integer,
    latitude numeric(9,6),
    longitude numeric(9,6),
    verification_status character varying(50),
    confidence_score numeric(4,3),
    last_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT hotels_rating_check CHECK (((rating IS NULL) OR ((rating >= (0)::numeric) AND (rating <= (5)::numeric)))),
    CONSTRAINT hotels_reviews_count_check CHECK (((reviews_count IS NULL) OR (reviews_count >= 0)))
);


--
-- Name: interactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    conversation_id uuid NOT NULL,
    actor_type character varying(30) NOT NULL,
    interaction_type character varying(50) NOT NULL,
    message text,
    entity_type character varying(50),
    entity_id uuid,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: partner_entities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.partner_entities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    partner_id uuid NOT NULL,
    entity_type character varying(30) NOT NULL,
    entity_id uuid NOT NULL,
    is_primary boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT partner_entities_entity_type_check CHECK (((entity_type)::text = ANY ((ARRAY['hotel'::character varying, 'restaurant'::character varying, 'tour_operator'::character varying, 'transport_provider'::character varying, 'general_service'::character varying])::text[])))
);


--
-- Name: partner_settlements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.partner_settlements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(40) NOT NULL,
    partner_id uuid NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    total_commission_amount numeric(12,2) DEFAULT 0 NOT NULL,
    currency character(3) DEFAULT 'PEN'::bpchar NOT NULL,
    due_date date NOT NULL,
    status character varying(30) DEFAULT 'open'::character varying NOT NULL,
    payment_method character varying(30),
    payment_reference character varying(200),
    proof_url text,
    reported_at timestamp with time zone,
    verified_at timestamp with time zone,
    paid_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT partner_settlements_check CHECK ((period_end >= period_start)),
    CONSTRAINT partner_settlements_payment_method_check CHECK (((payment_method IS NULL) OR ((payment_method)::text = ANY ((ARRAY['yape'::character varying, 'plin'::character varying, 'bank_transfer'::character varying, 'cash'::character varying, 'other'::character varying])::text[])))),
    CONSTRAINT partner_settlements_status_check CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'pending_payment'::character varying, 'reported'::character varying, 'waiting_verification'::character varying, 'paid'::character varying, 'overdue'::character varying, 'disputed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT partner_settlements_total_commission_amount_check CHECK ((total_commission_amount >= (0)::numeric))
);


--
-- Name: partners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.partners (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(30) NOT NULL,
    business_name character varying(200) NOT NULL,
    legal_name character varying(200),
    tax_id character varying(30),
    contact_name character varying(150),
    phone character varying(50),
    whatsapp character varying(50),
    email character varying(200),
    preferred_language character varying(10) DEFAULT 'es'::character varying,
    commission_type character varying(20) DEFAULT 'percentage'::character varying NOT NULL,
    commission_value numeric(10,2) DEFAULT 0 NOT NULL,
    payment_model character varying(30) DEFAULT 'direct_partner'::character varying NOT NULL,
    payout_method character varying(30),
    reservations_enabled boolean DEFAULT false NOT NULL,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT partners_commission_type_check CHECK (((commission_type)::text = ANY ((ARRAY['percentage'::character varying, 'fixed'::character varying, 'none'::character varying])::text[]))),
    CONSTRAINT partners_commission_value_check CHECK ((commission_value >= (0)::numeric)),
    CONSTRAINT partners_payment_model_check CHECK (((payment_model)::text = ANY ((ARRAY['direct_partner'::character varying, 'h4u_collect'::character varying, 'marketplace_split'::character varying])::text[]))),
    CONSTRAINT partners_payout_method_check CHECK (((payout_method IS NULL) OR ((payout_method)::text = ANY ((ARRAY['yape'::character varying, 'plin'::character varying, 'bank_transfer'::character varying, 'mercado_pago'::character varying, 'izipay'::character varying, 'other'::character varying])::text[])))),
    CONSTRAINT partners_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'active'::character varying, 'suspended'::character varying, 'inactive'::character varying])::text[])))
);


--
-- Name: payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(40) NOT NULL,
    reservation_id uuid NOT NULL,
    amount numeric(12,2) NOT NULL,
    currency character(3) DEFAULT 'PEN'::bpchar NOT NULL,
    payment_method character varying(30) NOT NULL,
    received_by character varying(20) NOT NULL,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    external_reference character varying(200),
    payment_reference character varying(200),
    proof_url text,
    reported_at timestamp with time zone,
    verified_at timestamp with time zone,
    paid_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    partner_confirmed_at timestamp with time zone,
    customer_confirmed_at timestamp with time zone,
    disputed_at timestamp with time zone,
    dispute_reason text,
    CONSTRAINT payments_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT payments_payment_method_check CHECK (((payment_method)::text = ANY ((ARRAY['yape'::character varying, 'plin'::character varying, 'bank_transfer'::character varying, 'card'::character varying, 'mercado_pago'::character varying, 'izipay'::character varying, 'cash'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT payments_received_by_check CHECK (((received_by)::text = ANY ((ARRAY['partner'::character varying, 'h4u'::character varying, 'processor'::character varying])::text[]))),
    CONSTRAINT payments_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'reported'::character varying, 'waiting_verification'::character varying, 'paid'::character varying, 'disputed'::character varying, 'failed'::character varying, 'cancelled'::character varying, 'refunded'::character varying, 'partially_refunded'::character varying])::text[])))
);


--
-- Name: product_partners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.product_partners (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    product_id uuid NOT NULL,
    partner_id uuid NOT NULL,
    partner_price numeric(12,2),
    currency character(3) DEFAULT 'PEN'::bpchar NOT NULL,
    commission_type character varying(20),
    commission_value numeric(10,2),
    priority smallint DEFAULT 100 NOT NULL,
    confirmation_required boolean DEFAULT true NOT NULL,
    status character varying(30) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT product_partners_commission_type_check CHECK (((commission_type IS NULL) OR ((commission_type)::text = ANY ((ARRAY['percentage'::character varying, 'fixed'::character varying, 'none'::character varying])::text[])))),
    CONSTRAINT product_partners_commission_value_check CHECK (((commission_value IS NULL) OR (commission_value >= (0)::numeric))),
    CONSTRAINT product_partners_partner_price_check CHECK (((partner_price IS NULL) OR (partner_price >= (0)::numeric))),
    CONSTRAINT product_partners_priority_check CHECK ((priority >= 0)),
    CONSTRAINT product_partners_status_check CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'paused'::character varying, 'inactive'::character varying])::text[])))
);


--
-- Name: products; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.products (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    code character varying(40) NOT NULL,
    name character varying(200) NOT NULL,
    slug character varying(220) NOT NULL,
    product_type character varying(40) NOT NULL,
    description text,
    source_entity_type character varying(40),
    source_entity_id uuid,
    booking_mode character varying(40) DEFAULT 'request'::character varying NOT NULL,
    confirmation_mode character varying(40) DEFAULT 'partner_confirmation'::character varying NOT NULL,
    price_from numeric(12,2),
    currency character(3) DEFAULT 'PEN'::bpchar,
    reservations_enabled boolean DEFAULT false NOT NULL,
    requires_payment boolean DEFAULT false NOT NULL,
    status character varying(30) DEFAULT 'draft'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT products_booking_mode_check CHECK (((booking_mode)::text = ANY ((ARRAY['instant'::character varying, 'request'::character varying, 'whatsapp_confirmation'::character varying, 'external_affiliate'::character varying])::text[]))),
    CONSTRAINT products_confirmation_mode_check CHECK (((confirmation_mode)::text = ANY ((ARRAY['automatic'::character varying, 'partner_confirmation'::character varying, 'h4u_confirmation'::character varying, 'external'::character varying])::text[]))),
    CONSTRAINT products_price_from_check CHECK (((price_from IS NULL) OR (price_from >= (0)::numeric))),
    CONSTRAINT products_product_type_check CHECK (((product_type)::text = ANY ((ARRAY['tour'::character varying, 'activity'::character varying, 'hotel'::character varying, 'restaurant_reservation'::character varying, 'transport'::character varying, 'laundry'::character varying, 'general_service'::character varying, 'route'::character varying, 'package'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT products_source_entity_type_check CHECK (((source_entity_type IS NULL) OR ((source_entity_type)::text = ANY ((ARRAY['tour'::character varying, 'hotel'::character varying, 'restaurant'::character varying, 'tour_operator'::character varying, 'transport_provider'::character varying, 'general_service'::character varying, 'attraction'::character varying])::text[])))),
    CONSTRAINT products_status_check CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'active'::character varying, 'paused'::character varying, 'inactive'::character varying])::text[])))
);


--
-- Name: refunds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refunds (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(40) NOT NULL,
    payment_id uuid NOT NULL,
    amount numeric(12,2) NOT NULL,
    currency character(3) DEFAULT 'PEN'::bpchar NOT NULL,
    reason text NOT NULL,
    status character varying(30) DEFAULT 'processed'::character varying NOT NULL,
    refund_method character varying(30),
    external_reference character varying(200),
    processed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT refunds_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT refunds_refund_method_check CHECK (((refund_method IS NULL) OR ((refund_method)::text = ANY ((ARRAY['yape'::character varying, 'plin'::character varying, 'bank_transfer'::character varying, 'card'::character varying, 'mercado_pago'::character varying, 'izipay'::character varying, 'cash'::character varying, 'other'::character varying])::text[])))),
    CONSTRAINT refunds_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processed'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: request_partners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_partners (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    service_request_id uuid NOT NULL,
    partner_id uuid NOT NULL,
    product_partner_id uuid,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    proposed_time time without time zone,
    proposed_price numeric(12,2),
    proposed_currency character(3),
    partner_message text,
    sent_at timestamp with time zone,
    delivered_at timestamp with time zone,
    viewed_at timestamp with time zone,
    responded_at timestamp with time zone,
    accepted_at timestamp with time zone,
    is_winner boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT request_partners_proposed_price_check CHECK (((proposed_price IS NULL) OR (proposed_price >= (0)::numeric))),
    CONSTRAINT request_partners_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'sent'::character varying, 'delivered'::character varying, 'viewed'::character varying, 'accepted'::character varying, 'rejected'::character varying, 'counter_offered'::character varying, 'expired'::character varying, 'lost'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: request_passengers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_passengers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    service_request_id uuid NOT NULL,
    passenger_number smallint NOT NULL,
    first_name character varying(100),
    last_name character varying(150),
    birth_date date,
    nationality_code character(2),
    document_type character varying(30),
    document_number character varying(100),
    is_primary_passenger boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT request_passengers_document_type_check CHECK (((document_type IS NULL) OR ((document_type)::text = ANY ((ARRAY['dni'::character varying, 'passport'::character varying, 'foreign_id'::character varying, 'other'::character varying])::text[])))),
    CONSTRAINT request_passengers_passenger_number_check CHECK ((passenger_number > 0))
);


--
-- Name: reservations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reservations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(40) NOT NULL,
    service_request_id uuid NOT NULL,
    traveler_id uuid,
    product_id uuid NOT NULL,
    partner_id uuid NOT NULL,
    request_partner_id uuid,
    service_date date NOT NULL,
    service_time time without time zone,
    passenger_count integer NOT NULL,
    agreed_price numeric(12,2),
    currency character(3) DEFAULT 'PEN'::bpchar NOT NULL,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    confirmed_at timestamp with time zone,
    completed_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    cancellation_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reservations_agreed_price_check CHECK (((agreed_price IS NULL) OR (agreed_price >= (0)::numeric))),
    CONSTRAINT reservations_passenger_count_check CHECK ((passenger_count > 0)),
    CONSTRAINT reservations_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'awaiting_passenger_data'::character varying, 'payment_pending'::character varying, 'confirmed'::character varying, 'ready'::character varying, 'completed'::character varying, 'cancelled'::character varying, 'no_show'::character varying])::text[])))
);


--
-- Name: restaurants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.restaurants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    code character varying(20) NOT NULL,
    name character varying(200) NOT NULL,
    place_type character varying(30) DEFAULT 'restaurant'::character varying NOT NULL,
    category character varying(100),
    address text,
    phone character varying(50),
    opening_hours text,
    price_range character varying(100),
    currency character(3),
    rating numeric(3,2),
    reviews_count integer,
    google_place_id character varying(255),
    google_maps_url text,
    zone character varying(100),
    distance_to_reference_m integer,
    latitude numeric(9,6),
    longitude numeric(9,6),
    verification_status character varying(50),
    confidence_score numeric(4,3),
    last_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT restaurants_place_type_chk CHECK (((place_type)::text = ANY ((ARRAY['restaurant'::character varying, 'cafe'::character varying, 'bar'::character varying, 'restobar'::character varying, 'other'::character varying])::text[])))
);


--
-- Name: service_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(40) NOT NULL,
    session_id uuid NOT NULL,
    traveler_id uuid,
    destination_id uuid NOT NULL,
    product_id uuid,
    request_type character varying(20) DEFAULT 'request'::character varying NOT NULL,
    service_date date NOT NULL,
    preferred_time time without time zone,
    flexible_time boolean DEFAULT true NOT NULL,
    passenger_count integer NOT NULL,
    adults_count integer DEFAULT 1 NOT NULL,
    minors_count integer DEFAULT 0 NOT NULL,
    additional_notes text,
    status character varying(30) DEFAULT 'created'::character varying NOT NULL,
    assigned_partner_id uuid,
    assigned_at timestamp with time zone,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT service_requests_adults_count_check CHECK ((adults_count >= 0)),
    CONSTRAINT service_requests_check CHECK ((passenger_count = (adults_count + minors_count))),
    CONSTRAINT service_requests_minors_count_check CHECK ((minors_count >= 0)),
    CONSTRAINT service_requests_passenger_count_check CHECK ((passenger_count > 0)),
    CONSTRAINT service_requests_request_type_check CHECK (((request_type)::text = ANY ((ARRAY['request'::character varying, 'reservation'::character varying])::text[]))),
    CONSTRAINT service_requests_status_check CHECK (((status)::text = ANY ((ARRAY['created'::character varying, 'searching'::character varying, 'offers_received'::character varying, 'partner_assigned'::character varying, 'awaiting_customer'::character varying, 'confirmed'::character varying, 'cancelled'::character varying, 'expired'::character varying])::text[])))
);


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    traveler_id uuid,
    destination_id uuid NOT NULL,
    hotel_id uuid,
    channel character varying(30) NOT NULL,
    language character varying(10),
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: settlement_commissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.settlement_commissions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    settlement_id uuid NOT NULL,
    commission_id uuid NOT NULL,
    commission_amount numeric(12,2) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT settlement_commissions_commission_amount_check CHECK ((commission_amount >= (0)::numeric))
);


--
-- Name: tour_operators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tour_operators (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    code character varying(20) NOT NULL,
    name character varying(200) NOT NULL,
    address text,
    phone character varying(50),
    email character varying(200),
    website text,
    opening_hours text,
    rating numeric(3,2),
    reviews_count integer,
    languages text,
    google_place_id character varying(255),
    google_maps_url text,
    zone character varying(100),
    latitude numeric(9,6),
    longitude numeric(9,6),
    verification_status character varying(50),
    confidence_score numeric(4,3),
    last_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tour_schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tour_schedules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tour_id uuid NOT NULL,
    service_date date,
    day_of_week smallint,
    start_time time without time zone,
    end_time time without time zone,
    available_slots integer,
    price numeric(12,2),
    currency character(3),
    valid_from date,
    valid_until date,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tour_schedules_day_of_week_check CHECK (((day_of_week IS NULL) OR ((day_of_week >= 0) AND (day_of_week <= 6))))
);


--
-- Name: tours; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tours (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    tour_operator_id uuid,
    code character varying(20) NOT NULL,
    name character varying(200) NOT NULL,
    destination_label character varying(200),
    description text,
    duration_minutes integer,
    price_from numeric(12,2),
    currency character(3),
    languages text,
    weather_dependent boolean,
    meeting_point text,
    hotel_pickup boolean,
    includes text,
    not_includes text,
    restrictions text,
    booking_url text,
    verification_status character varying(50),
    confidence_score numeric(4,3),
    last_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: transport_providers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transport_providers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    code character varying(20),
    name character varying(200) NOT NULL,
    provider_type character varying(100),
    phone character varying(50),
    website text,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: transport_routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transport_routes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider_id uuid NOT NULL,
    code character varying(20) NOT NULL,
    origin_name character varying(150) NOT NULL,
    destination_name character varying(150) NOT NULL,
    service_type character varying(50),
    duration_minutes integer,
    departure_times text,
    private_service boolean,
    shared_service boolean,
    booking_url text,
    verification_status character varying(50),
    confidence_score numeric(4,3),
    last_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: travelers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.travelers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    external_id character varying(255),
    preferred_language character varying(10),
    country_code character(2),
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: venues; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.venues (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    destination_id uuid NOT NULL,
    code character varying(20) NOT NULL,
    name character varying(200) NOT NULL,
    venue_type character varying(100),
    address text,
    capacity integer,
    notes text,
    google_place_id character varying(255),
    google_maps_url text,
    zone character varying(100),
    latitude numeric(9,6),
    longitude numeric(9,6),
    verification_status character varying(50),
    confidence_score numeric(4,3),
    last_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: analytics_events analytics_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analytics_events
    ADD CONSTRAINT analytics_events_pkey PRIMARY KEY (id);


--
-- Name: attractions attractions_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attractions
    ADD CONSTRAINT attractions_code_key UNIQUE (code);


--
-- Name: attractions attractions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attractions
    ADD CONSTRAINT attractions_pkey PRIMARY KEY (id);


--
-- Name: commissions commissions_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_code_key UNIQUE (code);


--
-- Name: commissions commissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_pkey PRIMARY KEY (id);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: data_sources data_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_sources
    ADD CONSTRAINT data_sources_pkey PRIMARY KEY (id);


--
-- Name: destinations destinations_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.destinations
    ADD CONSTRAINT destinations_code_key UNIQUE (code);


--
-- Name: destinations destinations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.destinations
    ADD CONSTRAINT destinations_pkey PRIMARY KEY (id);


--
-- Name: destinations destinations_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.destinations
    ADD CONSTRAINT destinations_slug_key UNIQUE (slug);


--
-- Name: emergency_services emergency_services_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emergency_services
    ADD CONSTRAINT emergency_services_code_key UNIQUE (code);


--
-- Name: emergency_services emergency_services_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emergency_services
    ADD CONSTRAINT emergency_services_pkey PRIMARY KEY (id);


--
-- Name: entity_embeddings entity_embeddings_entity_type_entity_id_content_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_embeddings
    ADD CONSTRAINT entity_embeddings_entity_type_entity_id_content_hash_key UNIQUE (entity_type, entity_id, content_hash);


--
-- Name: entity_embeddings entity_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_embeddings
    ADD CONSTRAINT entity_embeddings_pkey PRIMARY KEY (id);


--
-- Name: entity_sources entity_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_sources
    ADD CONSTRAINT entity_sources_pkey PRIMARY KEY (id);


--
-- Name: events events_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_code_key UNIQUE (code);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: general_services general_services_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.general_services
    ADD CONSTRAINT general_services_code_key UNIQUE (code);


--
-- Name: general_services general_services_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.general_services
    ADD CONSTRAINT general_services_pkey PRIMARY KEY (id);


--
-- Name: hotels hotels_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotels
    ADD CONSTRAINT hotels_code_key UNIQUE (code);


--
-- Name: hotels hotels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotels
    ADD CONSTRAINT hotels_pkey PRIMARY KEY (id);


--
-- Name: interactions interactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interactions
    ADD CONSTRAINT interactions_pkey PRIMARY KEY (id);


--
-- Name: partner_entities partner_entities_partner_id_entity_type_entity_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_entities
    ADD CONSTRAINT partner_entities_partner_id_entity_type_entity_id_key UNIQUE (partner_id, entity_type, entity_id);


--
-- Name: partner_entities partner_entities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_entities
    ADD CONSTRAINT partner_entities_pkey PRIMARY KEY (id);


--
-- Name: partner_settlements partner_settlements_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_settlements
    ADD CONSTRAINT partner_settlements_code_key UNIQUE (code);


--
-- Name: partner_settlements partner_settlements_partner_id_period_start_period_end_curr_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_settlements
    ADD CONSTRAINT partner_settlements_partner_id_period_start_period_end_curr_key UNIQUE (partner_id, period_start, period_end, currency);


--
-- Name: partner_settlements partner_settlements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_settlements
    ADD CONSTRAINT partner_settlements_pkey PRIMARY KEY (id);


--
-- Name: partners partners_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partners
    ADD CONSTRAINT partners_code_key UNIQUE (code);


--
-- Name: partners partners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partners
    ADD CONSTRAINT partners_pkey PRIMARY KEY (id);


--
-- Name: payments payments_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_code_key UNIQUE (code);


--
-- Name: payments payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);


--
-- Name: product_partners product_partners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_partners
    ADD CONSTRAINT product_partners_pkey PRIMARY KEY (id);


--
-- Name: product_partners product_partners_product_id_partner_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_partners
    ADD CONSTRAINT product_partners_product_id_partner_id_key UNIQUE (product_id, partner_id);


--
-- Name: products products_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_code_key UNIQUE (code);


--
-- Name: products products_destination_id_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_destination_id_slug_key UNIQUE (destination_id, slug);


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (id);


--
-- Name: refunds refunds_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refunds
    ADD CONSTRAINT refunds_code_key UNIQUE (code);


--
-- Name: refunds refunds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refunds
    ADD CONSTRAINT refunds_pkey PRIMARY KEY (id);


--
-- Name: request_partners request_partners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_partners
    ADD CONSTRAINT request_partners_pkey PRIMARY KEY (id);


--
-- Name: request_partners request_partners_service_request_id_partner_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_partners
    ADD CONSTRAINT request_partners_service_request_id_partner_id_key UNIQUE (service_request_id, partner_id);


--
-- Name: request_passengers request_passengers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_passengers
    ADD CONSTRAINT request_passengers_pkey PRIMARY KEY (id);


--
-- Name: request_passengers request_passengers_service_request_id_passenger_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_passengers
    ADD CONSTRAINT request_passengers_service_request_id_passenger_number_key UNIQUE (service_request_id, passenger_number);


--
-- Name: reservations reservations_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_code_key UNIQUE (code);


--
-- Name: reservations reservations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_pkey PRIMARY KEY (id);


--
-- Name: reservations reservations_service_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_service_request_id_key UNIQUE (service_request_id);


--
-- Name: restaurants restaurants_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restaurants
    ADD CONSTRAINT restaurants_code_key UNIQUE (code);


--
-- Name: restaurants restaurants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restaurants
    ADD CONSTRAINT restaurants_pkey PRIMARY KEY (id);


--
-- Name: service_requests service_requests_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_requests
    ADD CONSTRAINT service_requests_code_key UNIQUE (code);


--
-- Name: service_requests service_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_requests
    ADD CONSTRAINT service_requests_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: settlement_commissions settlement_commissions_commission_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settlement_commissions
    ADD CONSTRAINT settlement_commissions_commission_id_key UNIQUE (commission_id);


--
-- Name: settlement_commissions settlement_commissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settlement_commissions
    ADD CONSTRAINT settlement_commissions_pkey PRIMARY KEY (id);


--
-- Name: settlement_commissions settlement_commissions_settlement_id_commission_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settlement_commissions
    ADD CONSTRAINT settlement_commissions_settlement_id_commission_id_key UNIQUE (settlement_id, commission_id);


--
-- Name: tour_operators tour_operators_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_operators
    ADD CONSTRAINT tour_operators_code_key UNIQUE (code);


--
-- Name: tour_operators tour_operators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_operators
    ADD CONSTRAINT tour_operators_pkey PRIMARY KEY (id);


--
-- Name: tour_schedules tour_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_schedules
    ADD CONSTRAINT tour_schedules_pkey PRIMARY KEY (id);


--
-- Name: tours tours_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tours
    ADD CONSTRAINT tours_code_key UNIQUE (code);


--
-- Name: tours tours_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tours
    ADD CONSTRAINT tours_pkey PRIMARY KEY (id);


--
-- Name: transport_providers transport_providers_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transport_providers
    ADD CONSTRAINT transport_providers_code_key UNIQUE (code);


--
-- Name: transport_providers transport_providers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transport_providers
    ADD CONSTRAINT transport_providers_pkey PRIMARY KEY (id);


--
-- Name: transport_routes transport_routes_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transport_routes
    ADD CONSTRAINT transport_routes_code_key UNIQUE (code);


--
-- Name: transport_routes transport_routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transport_routes
    ADD CONSTRAINT transport_routes_pkey PRIMARY KEY (id);


--
-- Name: travelers travelers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.travelers
    ADD CONSTRAINT travelers_pkey PRIMARY KEY (id);


--
-- Name: venues venues_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.venues
    ADD CONSTRAINT venues_code_key UNIQUE (code);


--
-- Name: venues venues_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.venues
    ADD CONSTRAINT venues_pkey PRIMARY KEY (id);


--
-- Name: idx_analytics_events_destination_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_events_destination_time ON public.analytics_events USING btree (destination_id, occurred_at);


--
-- Name: idx_analytics_events_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_events_session ON public.analytics_events USING btree (session_id);


--
-- Name: idx_attractions_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_attractions_destination ON public.attractions USING btree (destination_id);


--
-- Name: idx_commissions_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commissions_partner ON public.commissions USING btree (partner_id);


--
-- Name: idx_commissions_partner_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commissions_partner_status ON public.commissions USING btree (partner_id, status, earned_at);


--
-- Name: idx_commissions_payment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commissions_payment ON public.commissions USING btree (payment_id);


--
-- Name: idx_commissions_reservation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commissions_reservation ON public.commissions USING btree (reservation_id);


--
-- Name: idx_commissions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commissions_status ON public.commissions USING btree (status);


--
-- Name: idx_conversations_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_session ON public.conversations USING btree (session_id);


--
-- Name: idx_emergency_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_emergency_destination ON public.emergency_services USING btree (destination_id);


--
-- Name: idx_entity_embeddings_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_entity_embeddings_lookup ON public.entity_embeddings USING btree (destination_id, entity_type, entity_id);


--
-- Name: idx_entity_sources_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_entity_sources_entity ON public.entity_sources USING btree (entity_type, entity_id);


--
-- Name: idx_events_destination_start; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_events_destination_start ON public.events USING btree (destination_id, start_at);


--
-- Name: idx_general_services_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_general_services_destination ON public.general_services USING btree (destination_id);


--
-- Name: idx_hotels_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hotels_destination ON public.hotels USING btree (destination_id);


--
-- Name: idx_interactions_conversation_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_interactions_conversation_created ON public.interactions USING btree (conversation_id, created_at);


--
-- Name: idx_partner_entities_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partner_entities_entity ON public.partner_entities USING btree (entity_type, entity_id);


--
-- Name: idx_partner_entities_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partner_entities_partner ON public.partner_entities USING btree (partner_id);


--
-- Name: idx_partner_settlements_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partner_settlements_due_date ON public.partner_settlements USING btree (due_date);


--
-- Name: idx_partner_settlements_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partner_settlements_partner ON public.partner_settlements USING btree (partner_id);


--
-- Name: idx_partner_settlements_partner_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partner_settlements_partner_status ON public.partner_settlements USING btree (partner_id, status, due_date);


--
-- Name: idx_partner_settlements_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partner_settlements_status ON public.partner_settlements USING btree (status);


--
-- Name: idx_partners_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partners_status ON public.partners USING btree (status);


--
-- Name: idx_payments_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payments_method ON public.payments USING btree (payment_method);


--
-- Name: idx_payments_reservation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payments_reservation ON public.payments USING btree (reservation_id);


--
-- Name: idx_payments_reservation_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payments_reservation_status ON public.payments USING btree (reservation_id, status);


--
-- Name: idx_payments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payments_status ON public.payments USING btree (status);


--
-- Name: idx_product_partners_assignment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_product_partners_assignment ON public.product_partners USING btree (product_id, status, priority);


--
-- Name: idx_product_partners_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_product_partners_partner ON public.product_partners USING btree (partner_id);


--
-- Name: idx_product_partners_product; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_product_partners_product ON public.product_partners USING btree (product_id);


--
-- Name: idx_product_partners_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_product_partners_status ON public.product_partners USING btree (status);


--
-- Name: idx_products_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_destination ON public.products USING btree (destination_id);


--
-- Name: idx_products_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_source ON public.products USING btree (source_entity_type, source_entity_id);


--
-- Name: idx_products_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_status ON public.products USING btree (status);


--
-- Name: idx_products_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_type ON public.products USING btree (product_type);


--
-- Name: idx_refunds_payment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refunds_payment ON public.refunds USING btree (payment_id);


--
-- Name: idx_refunds_payment_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refunds_payment_status ON public.refunds USING btree (payment_id, status);


--
-- Name: idx_refunds_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refunds_status ON public.refunds USING btree (status);


--
-- Name: idx_request_partners_dispatch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_partners_dispatch ON public.request_partners USING btree (service_request_id, status, created_at);


--
-- Name: idx_request_partners_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_partners_partner ON public.request_partners USING btree (partner_id);


--
-- Name: idx_request_partners_request; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_partners_request ON public.request_partners USING btree (service_request_id);


--
-- Name: idx_request_partners_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_partners_status ON public.request_partners USING btree (status);


--
-- Name: idx_request_passengers_request; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_passengers_request ON public.request_passengers USING btree (service_request_id);


--
-- Name: idx_reservations_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservations_partner ON public.reservations USING btree (partner_id);


--
-- Name: idx_reservations_partner_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservations_partner_status ON public.reservations USING btree (partner_id, status, service_date);


--
-- Name: idx_reservations_product; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservations_product ON public.reservations USING btree (product_id);


--
-- Name: idx_reservations_service_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservations_service_date ON public.reservations USING btree (service_date);


--
-- Name: idx_reservations_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservations_status ON public.reservations USING btree (status);


--
-- Name: idx_reservations_traveler; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservations_traveler ON public.reservations USING btree (traveler_id);


--
-- Name: idx_restaurants_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_restaurants_destination ON public.restaurants USING btree (destination_id);


--
-- Name: idx_service_requests_assignment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_requests_assignment ON public.service_requests USING btree (product_id, service_date, status);


--
-- Name: idx_service_requests_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_requests_destination ON public.service_requests USING btree (destination_id);


--
-- Name: idx_service_requests_product; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_requests_product ON public.service_requests USING btree (product_id);


--
-- Name: idx_service_requests_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_requests_session ON public.service_requests USING btree (session_id);


--
-- Name: idx_service_requests_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_requests_status ON public.service_requests USING btree (status);


--
-- Name: idx_service_requests_traveler; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_requests_traveler ON public.service_requests USING btree (traveler_id);


--
-- Name: idx_sessions_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_destination ON public.sessions USING btree (destination_id);


--
-- Name: idx_sessions_traveler; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_traveler ON public.sessions USING btree (traveler_id);


--
-- Name: idx_settlement_commissions_commission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_settlement_commissions_commission ON public.settlement_commissions USING btree (commission_id);


--
-- Name: idx_settlement_commissions_settlement; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_settlement_commissions_settlement ON public.settlement_commissions USING btree (settlement_id);


--
-- Name: idx_tour_operators_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tour_operators_destination ON public.tour_operators USING btree (destination_id);


--
-- Name: idx_tour_schedules_tour; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tour_schedules_tour ON public.tour_schedules USING btree (tour_id);


--
-- Name: idx_tours_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tours_destination ON public.tours USING btree (destination_id);


--
-- Name: idx_tours_operator; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tours_operator ON public.tours USING btree (tour_operator_id);


--
-- Name: idx_transport_providers_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transport_providers_destination ON public.transport_providers USING btree (destination_id);


--
-- Name: idx_transport_routes_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transport_routes_provider ON public.transport_routes USING btree (provider_id);


--
-- Name: idx_venues_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_venues_destination ON public.venues USING btree (destination_id);


--
-- Name: uq_commissions_payment; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_commissions_payment ON public.commissions USING btree (payment_id) WHERE (payment_id IS NOT NULL);


--
-- Name: uq_request_partners_one_winner; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_request_partners_one_winner ON public.request_partners USING btree (service_request_id) WHERE (is_winner = true);


--
-- Name: attractions trg_attractions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_attractions_updated_at BEFORE UPDATE ON public.attractions FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: conversations trg_conversations_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_conversations_updated_at BEFORE UPDATE ON public.conversations FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: data_sources trg_data_sources_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_data_sources_updated_at BEFORE UPDATE ON public.data_sources FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: destinations trg_destinations_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_destinations_updated_at BEFORE UPDATE ON public.destinations FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: emergency_services trg_emergency_services_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_emergency_services_updated_at BEFORE UPDATE ON public.emergency_services FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: entity_embeddings trg_entity_embeddings_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_entity_embeddings_updated_at BEFORE UPDATE ON public.entity_embeddings FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: events trg_events_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_events_updated_at BEFORE UPDATE ON public.events FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: general_services trg_general_services_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_general_services_updated_at BEFORE UPDATE ON public.general_services FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: hotels trg_hotels_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_hotels_updated_at BEFORE UPDATE ON public.hotels FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: restaurants trg_restaurants_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_restaurants_updated_at BEFORE UPDATE ON public.restaurants FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: sessions trg_sessions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sessions_updated_at BEFORE UPDATE ON public.sessions FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: tour_operators trg_tour_operators_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_tour_operators_updated_at BEFORE UPDATE ON public.tour_operators FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: tour_schedules trg_tour_schedules_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_tour_schedules_updated_at BEFORE UPDATE ON public.tour_schedules FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: tours trg_tours_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_tours_updated_at BEFORE UPDATE ON public.tours FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: transport_providers trg_transport_providers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_transport_providers_updated_at BEFORE UPDATE ON public.transport_providers FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: transport_routes trg_transport_routes_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_transport_routes_updated_at BEFORE UPDATE ON public.transport_routes FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: travelers trg_travelers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_travelers_updated_at BEFORE UPDATE ON public.travelers FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: venues trg_venues_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_venues_updated_at BEFORE UPDATE ON public.venues FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: analytics_events analytics_events_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analytics_events
    ADD CONSTRAINT analytics_events_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: analytics_events analytics_events_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analytics_events
    ADD CONSTRAINT analytics_events_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(id);


--
-- Name: analytics_events analytics_events_traveler_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analytics_events
    ADD CONSTRAINT analytics_events_traveler_id_fkey FOREIGN KEY (traveler_id) REFERENCES public.travelers(id);


--
-- Name: attractions attractions_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attractions
    ADD CONSTRAINT attractions_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: commissions commissions_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id);


--
-- Name: commissions commissions_payment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES public.payments(id);


--
-- Name: commissions commissions_reservation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_reservation_id_fkey FOREIGN KEY (reservation_id) REFERENCES public.reservations(id);


--
-- Name: conversations conversations_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(id) ON DELETE CASCADE;


--
-- Name: emergency_services emergency_services_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emergency_services
    ADD CONSTRAINT emergency_services_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: entity_embeddings entity_embeddings_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_embeddings
    ADD CONSTRAINT entity_embeddings_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: entity_sources entity_sources_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_sources
    ADD CONSTRAINT entity_sources_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.data_sources(id);


--
-- Name: events events_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: events events_venue_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_venue_id_fkey FOREIGN KEY (venue_id) REFERENCES public.venues(id);


--
-- Name: general_services general_services_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.general_services
    ADD CONSTRAINT general_services_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: hotels hotels_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotels
    ADD CONSTRAINT hotels_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: interactions interactions_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interactions
    ADD CONSTRAINT interactions_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;


--
-- Name: partner_entities partner_entities_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_entities
    ADD CONSTRAINT partner_entities_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id) ON DELETE CASCADE;


--
-- Name: partner_settlements partner_settlements_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_settlements
    ADD CONSTRAINT partner_settlements_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id);


--
-- Name: payments payments_reservation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_reservation_id_fkey FOREIGN KEY (reservation_id) REFERENCES public.reservations(id);


--
-- Name: product_partners product_partners_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_partners
    ADD CONSTRAINT product_partners_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id) ON DELETE CASCADE;


--
-- Name: product_partners product_partners_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_partners
    ADD CONSTRAINT product_partners_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id) ON DELETE CASCADE;


--
-- Name: products products_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: refunds refunds_payment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refunds
    ADD CONSTRAINT refunds_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES public.payments(id);


--
-- Name: request_partners request_partners_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_partners
    ADD CONSTRAINT request_partners_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id);


--
-- Name: request_partners request_partners_product_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_partners
    ADD CONSTRAINT request_partners_product_partner_id_fkey FOREIGN KEY (product_partner_id) REFERENCES public.product_partners(id);


--
-- Name: request_partners request_partners_service_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_partners
    ADD CONSTRAINT request_partners_service_request_id_fkey FOREIGN KEY (service_request_id) REFERENCES public.service_requests(id) ON DELETE CASCADE;


--
-- Name: request_passengers request_passengers_service_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_passengers
    ADD CONSTRAINT request_passengers_service_request_id_fkey FOREIGN KEY (service_request_id) REFERENCES public.service_requests(id) ON DELETE CASCADE;


--
-- Name: reservations reservations_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id);


--
-- Name: reservations reservations_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id);


--
-- Name: reservations reservations_request_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_request_partner_id_fkey FOREIGN KEY (request_partner_id) REFERENCES public.request_partners(id);


--
-- Name: reservations reservations_service_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_service_request_id_fkey FOREIGN KEY (service_request_id) REFERENCES public.service_requests(id);


--
-- Name: reservations reservations_traveler_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_traveler_id_fkey FOREIGN KEY (traveler_id) REFERENCES public.travelers(id);


--
-- Name: restaurants restaurants_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restaurants
    ADD CONSTRAINT restaurants_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: service_requests service_requests_assigned_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_requests
    ADD CONSTRAINT service_requests_assigned_partner_id_fkey FOREIGN KEY (assigned_partner_id) REFERENCES public.partners(id);


--
-- Name: service_requests service_requests_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_requests
    ADD CONSTRAINT service_requests_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: service_requests service_requests_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_requests
    ADD CONSTRAINT service_requests_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id);


--
-- Name: service_requests service_requests_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_requests
    ADD CONSTRAINT service_requests_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(id);


--
-- Name: service_requests service_requests_traveler_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_requests
    ADD CONSTRAINT service_requests_traveler_id_fkey FOREIGN KEY (traveler_id) REFERENCES public.travelers(id);


--
-- Name: sessions sessions_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: sessions sessions_hotel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_hotel_id_fkey FOREIGN KEY (hotel_id) REFERENCES public.hotels(id);


--
-- Name: sessions sessions_traveler_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_traveler_id_fkey FOREIGN KEY (traveler_id) REFERENCES public.travelers(id);


--
-- Name: settlement_commissions settlement_commissions_commission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settlement_commissions
    ADD CONSTRAINT settlement_commissions_commission_id_fkey FOREIGN KEY (commission_id) REFERENCES public.commissions(id);


--
-- Name: settlement_commissions settlement_commissions_settlement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settlement_commissions
    ADD CONSTRAINT settlement_commissions_settlement_id_fkey FOREIGN KEY (settlement_id) REFERENCES public.partner_settlements(id) ON DELETE CASCADE;


--
-- Name: tour_operators tour_operators_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_operators
    ADD CONSTRAINT tour_operators_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: tour_schedules tour_schedules_tour_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_schedules
    ADD CONSTRAINT tour_schedules_tour_id_fkey FOREIGN KEY (tour_id) REFERENCES public.tours(id) ON DELETE CASCADE;


--
-- Name: tours tours_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tours
    ADD CONSTRAINT tours_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: tours tours_tour_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tours
    ADD CONSTRAINT tours_tour_operator_id_fkey FOREIGN KEY (tour_operator_id) REFERENCES public.tour_operators(id);


--
-- Name: transport_providers transport_providers_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transport_providers
    ADD CONSTRAINT transport_providers_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: transport_routes transport_routes_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transport_routes
    ADD CONSTRAINT transport_routes_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.transport_providers(id);


--
-- Name: venues venues_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.venues
    ADD CONSTRAINT venues_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- PostgreSQL database dump complete
--

\unrestrict EKxqdlpNTAHGVZnCHATjLfMrflKh1zhRkprnWhqIUsAqWNYsb7s3emOs3gCDCW9

