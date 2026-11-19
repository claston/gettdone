package br.com.ofxsimples.shadow;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.nio.file.Path;
import java.net.InetSocketAddress;
import java.util.concurrent.atomic.AtomicInteger;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class OfficialEngineTest {
    static OfficialEngine engine;
    static ObjectMapper mapper = new ObjectMapper();
    static AtomicInteger onlineVersionCalls = new AtomicInteger();
    @BeforeAll static void start() throws Exception {
        var trap = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        trap.createContext("/", exchange -> {
            onlineVersionCalls.incrementAndGet();
            byte[] body = "{}".getBytes(java.nio.charset.StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            try (var response = exchange.getResponseBody()) { response.write(body); }
        });
        trap.start();
        System.setProperty("versao.url", "http://127.0.0.1:" + trap.getAddress().getPort());
        try { engine = new OfficialEngine(Path.of(System.getProperty("receita.database"))); }
        finally { trap.stop(0); System.clearProperty("versao.url"); }
    }
    @AfterAll static void stop() { engine.close(); }
    @Test void startupDoesNotQueryOnlineVersion() { assertEquals(0, onlineVersionCalls.get()); }
    static ObjectNode request(String ncm, String classification) {
        var request = mapper.createObjectNode();
        request.put("schema_version", 1).put("run_id", "test").put("input_hash", "test")
            .put("engine_version", OfficialEngine.VERSION).put("document_type", "NFe").put("reference_date", "2027-03-10");
        request.putArray("items").addObject().put("id", "1").put("ncm", ncm).put("cClassTrib", classification)
            .put("cst", "200").put("description", "Synthetic item");
        return request;
    }
    @Test void officialRiceExampleHasAnnexCandidateButStillNeedsReview() {
        var item = engine.classify(request("10063021", "200003")).path("items").get(0);
        assertEquals("review_required", item.path("status").asText());
        assertEquals("compatible_requires_review", item.path("reported_code_check").asText());
        assertTrue(item.path("candidates").toString().contains("200003"));
        assertFalse(item.path("candidates").toString().contains("\"code\":\"000001\""));
    }
    @Test void officialReg011MismatchIsNotAccepted() {
        var item = engine.classify(request("02013000", "200035")).path("items").get(0);
        assertEquals("incompatible", item.path("reported_code_check").asText());
    }
    @Test void missingOrInvalidCodesNeverDefaultToIntegral() {
        assertEquals("insufficient_data", engine.classify(request("", "")).path("items").get(0).path("status").asText());
        assertEquals("invalid_code", engine.classify(request("99999999", "")).path("items").get(0).path("status").asText());
    }
    @Test void allItemsSurviveMixedResultsAndVersionMismatchFails() {
        var request = request("10063021", "200003");
        ((com.fasterxml.jackson.databind.node.ArrayNode) request.path("items")).addObject().put("id", "2");
        assertEquals(2, engine.classify(request).path("items").size());
        request.put("engine_version", "different");
        assertThrows(IllegalArgumentException.class, () -> engine.classify(request));
    }
}
