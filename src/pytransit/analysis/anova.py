import scipy
import numpy
import statsmodels.stats.multitest

import time
import sys
import collections

import base
import pytransit
import pytransit.transit_tools as transit_tools
import pytransit.tnseq_tools as tnseq_tools
import pytransit.norm_tools as norm_tools

############# GUI ELEMENTS ##################

short_name = "anova"
long_name = "anova"
short_desc = "Perform Anova analysis"
long_desc = """Perform Anova analysis"""
EOL = "\n"

transposons = ["", ""]
columns = []

class AnovaAnalysis(base.TransitAnalysis):
    def __init__(self):
        base.TransitAnalysis.__init__(self, short_name, long_name, short_desc, long_desc, transposons, AnovaMethod)
def main():
    print("ANOVA example")

class AnovaMethod(base.MultiConditionMethod):
    """
    anova
    """
    def __init__(self, combined_wig, metadata, annotation, normalization, output_file, ignored_conditions=set()):
        base.MultiConditionMethod.__init__(self, short_name, long_name, short_desc, long_desc, combined_wig, metadata, annotation, output_file, normalization=normalization)
        self.ignored_conditions = ignored_conditions

    @classmethod
    def fromargs(self, rawargs):
        (args, kwargs) = transit_tools.cleanargs(rawargs)
        combined_wig = kwargs['-wig']
        metadata = kwargs['-meta']
        annotation = kwargs['-prot']
        normalization = kwargs.get("n", "TTR")
        ignored_conditions = set(kwargs.get("-ignore-conditions", " ").split(","))
        output_file = kwargs['o']

        return self(combined_wig, metadata, annotation, normalization, output_file, ignored_conditions)

    def wigs_to_conditions(self, conditionsByFile, filenamesInCombWig):
        """
            Returns list of conditions corresponding to given wigfiles.
            ({FileName: Condition}, [FileName]) -> [Condition]
            Condition :: [String]
        """
        return [conditionsByFile.get(f, "Unknown") for f in filenamesInCombWig]

    def means_by_condition_for_gene(self, sites, conditions, data):
        """
            Returns a dictionary of {Condition: Mean} for each condition.
            ([Site], [Condition]) -> {Condition: Number}
            Site :: Number
            Condition :: String
        """
        wigsByConditions = collections.defaultdict(lambda: [])
        for i, c in enumerate(conditions):
            wigsByConditions[c].append(i)

        return { c: numpy.mean(data[wigIndex][:, sites]) for (c, wigIndex) in wigsByConditions.items() }

    def filter_by_conditions_blacklist(self, data, conditions, ignored_conditions):
        """
            Filters out wigfiles, with ignored conditions.
            ([[Wigdata]], [Condition]) -> Tuple([[Wigdata]], [Condition])
        """
        d_filtered, cond_filtered = [], [];
        for i, c in enumerate(conditions):
          if c not in ignored_conditions:
            d_filtered.append(data[i])
            cond_filtered.append(conditions[i])

        return (numpy.array(d_filtered), numpy.array(cond_filtered))

    def read_samples_metadata(self, metadata_file):
        """
          Filename -> ConditionMap
          ConditionMap :: {Filename: Condition}
        """
        conditions = set()
        wigFiles = []
        conditionsByFile = {}
        with open(metadata_file) as mfile:
            next(mfile) # Skip headers
            for line in mfile:
                if line[0]=='#': continue
                [id, condition, wfile] = line.split()
                conditionsByFile[wfile] = condition
        return conditionsByFile

    def means_by_rv(self, data, RvSiteindexesMap, genes, conditions):
        """
            Returns Dictionary of mean values by condition
            ([[Wigdata]], {Rv: SiteIndex}, [Gene], [Condition]) -> {Rv: {Condition: Number}}
            Wigdata :: [Number]
            SiteIndex :: Number
            Gene :: {start, end, rv, gene, strand}
            Condition :: String
        """
        MeansByRv = {}
        for gene in genes:
            Rv = gene["rv"]
            if len(RvSiteindexesMap[gene["rv"]]) > 0: # skip genes with no TA sites
                MeansByRv[Rv] = self.means_by_condition_for_gene(RvSiteindexesMap[Rv], conditions, data)
        return MeansByRv

    def group_by_condition(self, wigList, conditions):
        """
            Returns array of datasets, where each dataset corresponds to one condition.
            ([[Wigdata]], [Condition]) -> [[DataForCondition]]
            Wigdata :: [Number]
            Condition :: String
            DataForCondition :: [Number]
        """
        countsByCondition = collections.defaultdict(lambda: [])
        for i, c in enumerate(conditions):
          countsByCondition[c].append(wigList[i])

        return [numpy.array(v).flatten() for v in countsByCondition.values()]

    def run_anova(self, data, genes, MeansByRv, RvSiteindexesMap, conditions):
        """
            Runs Anova (grouping data by condition) and returns p and q values
            ([[Wigdata]], [Gene], {Rv: {Condition: Mean}}, {Rv: [SiteIndex]}, [Condition]) -> Tuple([Number], [Number])
            Wigdata :: [Number]
            Gene :: {start, end, rv, gene, strand}
            Mean :: Number
            SiteIndex: Integer
            Condition :: String
        """
        pvals,Rvs = [],[]
        for gene in genes:
            Rv = gene["rv"]
            if Rv in MeansByRv:
                countsvec = self.group_by_condition(map(lambda wigData: wigData[RvSiteindexesMap[Rv]], data), conditions)
                stat,pval = scipy.stats.f_oneway(*countsvec)
                pvals.append(pval)
                Rvs.append(Rv)

        pvals = numpy.array(pvals)
        mask = numpy.isfinite(pvals)
        qvals = numpy.full(pvals.shape, numpy.nan)
        qvals[mask] = statsmodels.stats.multitest.fdrcorrection(pvals[mask])[1] # BH, alpha=0.05

        p,q = {},{}
        for i,rv in enumerate(Rvs):
            p[rv],q[rv] = pvals[i],qvals[i]
        return (p, q)

    def Run(self):
        self.transit_message("Starting Anova analysis")
        start_time = time.time()

        self.transit_message("Getting Data")
        (sites, data, filenamesInCombWig) = tnseq_tools.read_combined_wig(self.combined_wig)

        self.transit_message("Normalizing using: %s" % self.normalization)
        (data, factors) = norm_tools.normalize_data(data, self.normalization)

        conditions = self.wigs_to_conditions(
            self.read_samples_metadata(self.metadata),
            filenamesInCombWig)
        data, conditions = self.filter_by_conditions_blacklist(data, conditions, self.ignored_conditions)

        genes = tnseq_tools.read_genes(self.annotation_path)

        TASiteindexMap = {TA: i for i, TA in enumerate(sites)}
        RvSiteindexesMap = tnseq_tools.rv_siteindexes_map(genes, TASiteindexMap)
        MeansByRv = self.means_by_rv(data, RvSiteindexesMap, genes, conditions)

        # pvals,Rvs, = [],[] # lists
        # for gene in genes:
        #   Rv = gene["rv"]
        #   if Rv in MeansByRv:
        #     countsvec = self.group_by_condition(map(lambda wigData: wigData[RvSiteindexesMap[Rv]], data), conditions)
        #     stat,pval = scipy.stats.f_oneway(*countsvec)
        #     pvals.append(pval)
        #     Rvs.append(Rv)

        # pvals = numpy.array(pvals)
        # mask = numpy.isfinite(pvals)
        # qvals = numpy.full(pvals.shape, numpy.nan)
        # qvals[mask] = statsmodels.stats.multitest.fdrcorrection(pvals[mask])[1] # BH, alpha=0.05

        # p,q = {},{}
        # for i,rv in enumerate(Rvs):
        #    p[rv],q[rv] = pvals[i],qvals[i]
        pvals,qvals = self.run_anova(data, genes, MeansByRv, RvSiteindexesMap, conditions)

        file = open(self.output,"w")
        conditionsList = list(set(conditions))
        vals = "Rv Gene TAs".split() + conditionsList + "pval padj".split()
        file.write('\t'.join(vals)+EOL)
        for gene in genes:
            Rv = gene["rv"]
            if Rv in MeansByRv:
              vals = [Rv, gene["gene"], str(len(RvSiteindexesMap[Rv]))] + ["%0.1f" % MeansByRv[Rv][c] for c in conditionsList] + ["%f" % x for x in [pvals[Rv], qvals[Rv]]]
              file.write('\t'.join(vals)+EOL)
        file.close()

if __name__ == "__main__":
    main()

