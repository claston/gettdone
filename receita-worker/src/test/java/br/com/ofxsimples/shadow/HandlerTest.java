package br.com.ofxsimples.shadow;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.LambdaLogger;
import com.amazonaws.services.lambda.runtime.events.SQSEvent;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayInputStream;
import java.time.OffsetDateTime;
import java.util.List;
import software.amazon.awssdk.core.ResponseInputStream;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class HandlerTest {
    static final String ID = "11111111-1111-4111-8111-111111111111";
    SQSEvent event(String body) {
        var message = new SQSEvent.SQSMessage();
        message.setMessageId("message-1"); message.setBody(body);
        var event = new SQSEvent(); event.setRecords(List.of(message)); return event;
    }
    SQSEvent validEvent() { return event("{\"schema_version\":1,\"run_id\":\"" + ID + "\"}"); }
    Context context() { var context = mock(Context.class); when(context.getLogger()).thenReturn(mock(LambdaLogger.class)); return context; }
    void input(S3Client client, boolean expired) throws Exception {
        var json = new ObjectMapper().createObjectNode().put("run_id", ID)
            .put("expires_at", OffsetDateTime.now().plusHours(expired ? -1 : 1).toString());
        when(client.getObject(any(GetObjectRequest.class))).thenAnswer(ignored ->
            new ResponseInputStream<>(GetObjectResponse.builder().build(), new ByteArrayInputStream(json.toString().getBytes())));
    }
    @Test void duplicateAndExpiredDeliveriesDoNotRunEngine() throws Exception {
        var client = mock(S3Client.class); input(client, false);
        var engine = mock(OfficialEngine.class);
        var handler = new Handler(client, "private-test-bucket", () -> engine);
        assertTrue(handler.handleRequest(validEvent(), context()).getBatchItemFailures().isEmpty());
        verifyNoInteractions(engine);
        input(client, true);
        assertTrue(handler.handleRequest(validEvent(), context()).getBatchItemFailures().isEmpty());
        verifyNoInteractions(engine);
    }
    @Test void missingResultRunsEngineAndWritesOnlyFixedBucketResult() throws Exception {
        var client = mock(S3Client.class); input(client, false);
        when(client.headObject(any(HeadObjectRequest.class))).thenThrow(S3Exception.builder().statusCode(404).build());
        var engine = mock(OfficialEngine.class);
        when(engine.classify(any())).thenReturn(new ObjectMapper().createObjectNode().put("run_id", ID));
        var handler = new Handler(client, "private-test-bucket", () -> engine);
        assertTrue(handler.handleRequest(validEvent(), context()).getBatchItemFailures().isEmpty());
        verify(client).putObject(argThat((PutObjectRequest put) -> put.bucket().equals("private-test-bucket")
            && put.key().equals("classification-shadow/" + ID + "/result.json")), any(RequestBody.class));
    }
    @Test void malformedInputAndTemporaryFailuresAreRetriedWithoutSensitiveLogs() throws Exception {
        var client = mock(S3Client.class);
        var handler = new Handler(client, "private-test-bucket", () -> null);
        assertEquals("message-1", handler.handleRequest(event("{}"), context()).getBatchItemFailures().get(0).getItemIdentifier());
        input(client, false);
        when(client.headObject(any(HeadObjectRequest.class))).thenThrow(S3Exception.builder().statusCode(503).build());
        assertEquals(1, handler.handleRequest(validEvent(), context()).getBatchItemFailures().size());
    }
}
