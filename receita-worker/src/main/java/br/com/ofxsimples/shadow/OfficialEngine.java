package br.com.ofxsimples.shadow;

import br.gov.serpro.rtc.CalculadoraTributoApplication;
import br.gov.serpro.rtc.domain.repository.NcmAplicavelRepository;
import br.gov.serpro.rtc.domain.service.ClassificacaoTributariaService;
import br.gov.serpro.rtc.domain.service.NomenclaturaService;
import br.gov.serpro.rtc.domain.service.dadosabertos.DadosAbertosService;
import br.gov.serpro.rtc.domain.service.exception.NcmNaoEncontradaException;
import br.gov.serpro.rtc.domain.service.exception.ClassificacaoTributariaNaoEncontradaException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.List;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;

/** Calls the pinned official services. No copied formulas, text inference or automatic tax decisions. */
public final class OfficialEngine implements AutoCloseable {
    public static final String COMMIT = "150cccbe414d4a5edea703e4f7a424af5aa222c7";
    public static final String VERSION = "receita-shadow-v1:" + COMMIT;
    private final ConfigurableApplicationContext context;
    private final DadosAbertosService data;
    private final ClassificacaoTributariaService classifications;
    private final NcmAplicavelRepository annexes;
    private final NomenclaturaService nomenclature;
    private final ObjectMapper mapper = new ObjectMapper();

    public OfficialEngine(Path database) {
        if (!database.toFile().isFile()) throw new IllegalStateException("Missing pinned official database");
        // The original application's servlet/security configuration is retained.
        // Bind only to loopback; Lambda invokes services directly, never an HTTP endpoint.
        context = SpringApplication.run(CalculadoraTributoApplication.class,
            "--spring.profiles.active=offline", "--server.address=127.0.0.1", "--server.port=0",
            "--spring.datasource.url=jdbc:sqlite:file:" + database.toAbsolutePath() + "?mode=ro&date_class=TEXT&date_string_format=yyyy-MM-dd",
            "--spring.sql.init.mode=never", "--spring.flyway.enabled=false", "--management.tracing.enabled=false",
            "--spring.main.banner-mode=off", "--logging.level.root=WARN");
        data = context.getBean(DadosAbertosService.class);
        classifications = context.getBean(ClassificacaoTributariaService.class);
        annexes = context.getBean(NcmAplicavelRepository.class);
        nomenclature = context.getBean(NomenclaturaService.class);
    }

    public ObjectNode classify(JsonNode request) {
        if (!VERSION.equals(request.path("engine_version").asText()) || request.path("schema_version").asInt() != 1
                || !request.path("document_type").asText().equals("NFe")
                || !request.path("items").isArray() || request.path("items").isEmpty() || request.path("items").size() > 200)
            throw new IllegalArgumentException("Unsupported shadow request");
        LocalDate date = LocalDate.parse(request.path("reference_date").asText());
        var output = mapper.createObjectNode();
        output.put("schema_version", 1).put("run_id", request.path("run_id").asText())
            .put("input_hash", request.path("input_hash").asText()).put("engine_version", VERSION);
        var items = output.putArray("items");
        var available = data.consultarClassificacoesTributariasCbsIbs(date);
        var foundations = data.consultarFundamentacoesLegais(date);
        for (var item : request.path("items")) {
            var row = items.addObject();
            row.put("id", item.path("id").asText()).put("description", item.path("description").asText("Item sem descrição"))
                .put("status", "review_required").put("label", "Conferir enquadramento")
                .put("evidence", "Compatibilidade de códigos na data de emissão; operação, regime e fato gerador exigem revisão.")
                .put("source_ref", item.path("source_ref").asText());
            row.set("original", item.deepCopy());
            var candidates = row.putArray("candidates");
            String ncm = item.path("ncm").asText("");
            if (!ncm.matches("[0-9]{8}")) {
                row.put("status", "insufficient_data").put("label", "NCM ausente ou incompleto");
                continue;
            }
            try { data.consultarNcm(ncm, date); }
            catch (NcmNaoEncontradaException invalid) {
                row.put("status", "invalid_code").put("label", "NCM não vigente na base consultada");
                continue;
            }
            for (var candidate : available) {
                var classification = classifications.buscarClassificacaoTributariaCbsIbs(candidate.getCodigo(), date);
                // Only positive annex matches are candidates. Official no-annex
                // acceptance is not evidence for selecting a tax treatment.
                if (!annexes.tem(classification.id(), date)
                        || !List.of("NCM", "EXCECAO_NCM", "MISTO").contains(
                            nomenclature.determinarNomenclatura("NFe", candidate.getCodigo(), date))
                        || !data.validarNcmAplicavel(candidate.getCodigo(), ncm, date).isValido()) continue;
                var option = candidates.addObject();
                option.put("code", candidate.getCodigo()).put("cst", classification.cst()).put("label", candidate.getDescricao());
                var legal = option.putArray("foundations");
                foundations.stream().filter(f -> candidate.getCodigo().equals(f.getCodigoClassificacaoTributaria()))
                    .map(f -> f.getTextoCurto() != null ? f.getTextoCurto() : f.getReferenciaNormativa())
                    .filter(f -> f != null).distinct().limit(10).forEach(legal::add);
            }
            String informed = item.path("cClassTrib").asText("");
            if (!informed.isEmpty()) {
                try {
                    var original = classifications.buscarClassificacaoTributariaCbsIbs(informed, date);
                    boolean valid = data.validarNcmAplicavel(informed, ncm, date).isValido()
                        && original.cst().equals(item.path("cst").asText());
                    String naming = nomenclature.determinarNomenclatura("NFe", informed, date);
                    // SEM cannot prove document compatibility or operation eligibility.
                    row.put("reported_code_check", valid && !naming.equals("SEM") ? "compatible_requires_review" :
                        valid ? "context_required" : "incompatible");
                } catch (ClassificacaoTributariaNaoEncontradaException invalid) {
                    row.put("reported_code_check", "invalid_classification");
                }
            }
            if (candidates.isEmpty()) row.put("label", "Sem tratamento determinado pelos anexos");
        }
        return output;
    }

    @Override public void close() { context.close(); }
}
