package br.com.ofxsimples.shadow;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestHandler;
import com.amazonaws.services.lambda.runtime.events.SQSEvent;
import com.amazonaws.services.lambda.runtime.events.SQSBatchResponse;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.UUID;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.S3Exception;

/** SQS carries only a run ID. Bucket/prefix are fixed by deployment, never by a message. */
public final class Handler implements RequestHandler<SQSEvent, SQSBatchResponse> {
    private final ObjectMapper mapper = new ObjectMapper();
    private final S3Client s3 = S3Client.builder().httpClientBuilder(UrlConnectionHttpClient.builder()
        .connectionTimeout(Duration.ofSeconds(3)).socketTimeout(Duration.ofSeconds(15))).build();
    private OfficialEngine engine;

    @Override public SQSBatchResponse handleRequest(SQSEvent event, Context context) {
        var failures = new ArrayList<SQSBatchResponse.BatchItemFailure>();
        for (var record : event.getRecords()) {
            try { process(record.getBody()); }
            catch (Exception failure) {
                // Do not log descriptions, XML, account identifiers or exception messages.
                context.getLogger().log("shadow_failure=" + failure.getClass().getSimpleName() + "\n");
                failures.add(new SQSBatchResponse.BatchItemFailure(record.getMessageId()));
            }
        }
        return new SQSBatchResponse(failures);
    }

    private void process(String body) throws Exception {
        var message = mapper.readTree(body);
        if (message.path("schema_version").asInt() != 1) throw new IllegalArgumentException("Unsupported message");
        String identifier = UUID.fromString(message.path("run_id").asText()).toString();
        String prefix = "classification-shadow/" + identifier + "/";
        String bucket = System.getenv("CLASSIFICATION_SHADOW_BUCKET");
        byte[] input;
        try (var stream = s3.getObject(GetObjectRequest.builder().bucket(bucket).key(prefix + "request.json").build())) {
            input = stream.readNBytes(1024 * 1024 + 1);
        }
        if (input.length > 1024 * 1024) throw new IllegalArgumentException("Request too large");
        var request = mapper.readTree(input);
        if (!identifier.equals(request.path("run_id").asText())) throw new IllegalArgumentException("Run mismatch");
        if (OffsetDateTime.parse(request.path("expires_at").asText()).toInstant().isBefore(Instant.now())) return;
        try {
            s3.headObject(b -> b.bucket(bucket).key(prefix + "result.json"));
            return; // Immutable result makes duplicate SQS delivery harmless.
        } catch (S3Exception missing) { if (missing.statusCode() != 404) throw missing; }
        if (engine == null) engine = new OfficialEngine(Path.of(System.getenv().getOrDefault("LAMBDA_TASK_ROOT", "."),
                                                               "calculadora", "db", "calculadora-pro.db"));
        byte[] output = mapper.writeValueAsBytes(engine.classify(request));
        if (output.length > 2 * 1024 * 1024) throw new IllegalStateException("Result too large");
        var put = PutObjectRequest.builder().bucket(bucket).key(prefix + "result.json").contentType("application/json")
            .serverSideEncryption("AES256").overrideConfiguration(c -> c.putHeader("If-None-Match", "*")).build();
        try { s3.putObject(put, RequestBody.fromBytes(output)); }
        catch (S3Exception duplicate) { if (duplicate.statusCode() != 412) throw duplicate; }
    }
}
