package br.com.ofxsimples.shadow;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Path;

/** CI runs the unpacked Lambda ZIP, not just the Maven classpath. */
public final class PackagedSmoke {
    public static void main(String[] args) throws Exception {
        var request = new ObjectMapper().readTree("""
            {"schema_version":1,"run_id":"synthetic","input_hash":"synthetic","document_type":"NFe",
             "reference_date":"2027-03-10","items":[{"id":"1","ncm":"02013000","cst":"200","cClassTrib":"200035"}]}
            """);
        ((com.fasterxml.jackson.databind.node.ObjectNode) request).put("engine_version", OfficialEngine.VERSION);
        try (var engine = new OfficialEngine(Path.of("calculadora/db/calculadora-pro.db"))) {
            var result = engine.classify(request);
            if (!result.path("items").get(0).path("reported_code_check").asText().equals("incompatible"))
                throw new IllegalStateException("Official REG-011 fixture failed");
            System.out.println("Packaged ZIP: official REG-011 mismatch preserved; no OCR or AI.");
        }
    }
}
